"""2F.2 SCADA tag server — the industrial data path made real (master plan 2F, LOG-033 decisions).

physics (plant-sim) → OpenPLC (%MW words / trip coils, real Modbus TCP) → THIS SERVICE →
  · tag DB (tags.py: ISA-style names, units, PLC addresses, GOOD/STALE/BAD quality)
  · historian (TimescaleDB hypertable on the 64Gi slowdisk — closes the PIVOT_SETUP §7 line)
  · /metrics (identical series names/labels to plant-sim's exposition → the aggregator repoint
    at cutover is ONE queries.yaml-URL-shaped swap: apply the scada ServiceMonitor, delete the
    plant-sim one; the sim's /metrics stays as an unscraped debug tap)
  · /tags (the UI's tag browser: values + quality + addresses + historian rate)

Honesty rules: the base plant path READS ONLY (FC03 holding block + FC01 coils — it never writes
the PLC; the sim is the field wiring, the trip program is the authority). A failed poll ages
quality (STALE→BAD) instead of repeating values as fresh; BAD tags leave /metrics entirely.
Historian down → serving continues, rows just stop counting (retry forever, like the sim's
PLC loop). Env: PLC_HOST/PLC_PORT/PLC_MW_BASE · HISTORIAN_DSN · PGPASSWORD · POLL_S/STALE_S/BAD_S · PORT.
The DSN carries no password. libpq reads it from PGPASSWORD, which the Deployment takes from
Secret plant/historian-auth (LOG-076).

Virtual PLC fleet (FLEET.md 8). The base plant path above does not change. The fleet path
adds these endpoints:
  POST   /enroll               a vPLC enrolls. X-Device-Token = hex HMAC-SHA256(FLEET_ENROLL_KEY, name).
  GET    /fleet                every enrolled PLC with its tags. Header X-Fleet-Enroll: enabled|disabled.
  GET    /domains              {"domains": {"plc:<name>": [machines..., "<name>"]}}
  GET    /metrics/fleet        vplc_* and scada_* series, labels namespace="fleet", pod="<name>".
  POST   /fleet/<name>/write   body {tag, value}. X-Scada-Token = SCADA_WRITE_TOKEN. Writable tags only.
  DELETE /fleet/<name>         X-Scada-Token. Stops the poll and removes the tags.
The fleet path writes the PLC only through POST /fleet/<name>/write, and only %MW setpoints.
One poll thread per enrolled PLC reads the full image every FLEET_POLL_S (default 1 s) with the
driver for its protocol (drivers/modbus.py, drivers/s7.py). A heartbeat enrollment with the
same task hash keeps the thread and first_good_at. Fleet tags go to the same plant_tags table
through a bounded queue, so a slow historian never delays a poll. An empty FLEET_ENROLL_KEY
disables enrollment. An empty SCADA_WRITE_TOKEN disables writes and deletes. Both fail closed.
Env: FLEET_ENROLL_KEY · SCADA_WRITE_TOKEN · FLEET_POLL_S.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

import fleet
import tags
from drivers import make_driver

PLC_HOST = os.environ.get("PLC_HOST", "openplc.plant.svc")
PLC_PORT = int(os.environ.get("PLC_PORT", "502"))
MW_BASE = int(os.environ.get("PLC_MW_BASE", "1024"))
POLL_S = float(os.environ.get("POLL_S", "1.0"))
STALE_S = float(os.environ.get("STALE_S", "10"))
BAD_S = float(os.environ.get("BAD_S", "30"))
PORT = int(os.environ.get("PORT", "9300"))
DSN = os.environ.get("HISTORIAN_DSN", "host=historian-db.plant.svc dbname=postgres user=postgres")
ENROLL_KEY = os.environ.get("FLEET_ENROLL_KEY", "")
WRITE_TOKEN = os.environ.get("SCADA_WRITE_TOKEN", "")
FLEET_POLL_S = float(os.environ.get("FLEET_POLL_S", "1.0"))
MAX_BODY = 256 * 1024

try:
    from pymodbus.client import ModbusTcpClient          # pymodbus 3.x (same dep as the sim)
except Exception:
    ModbusTcpClient = None
try:
    import psycopg2
except Exception:
    psycopg2 = None

_lock = threading.Lock()
STATE = {
    "tags": {},                 # tag -> {value, quality, ts, asset, signal}
    "plc_connected": False,
    "last_good_ts": None,
    "historian": {"connected": False, "rows_total": 0, "rows_per_s": 0.0},
}
TABLE = tags.tag_table()
BY_TAG = {r["tag"]: r for r in TABLE}


# ------------------------------------------------------------------ PLC poll --
def poll_loop():
    if ModbusTcpClient is None:
        print("tagserver: pymodbus missing — no PLC path (tags stay empty)", flush=True)
        return
    client = None
    while True:
        t0 = time.time()
        try:
            if client is None:
                client = ModbusTcpClient(PLC_HOST, port=PLC_PORT, timeout=2)
                if not client.connect():
                    raise ConnectionError(f"connect {PLC_HOST}:{PLC_PORT}")
            rr = client.read_holding_registers(MW_BASE, count=tags.N_REGS, slave=1)
            rc = client.read_coils(0, count=len(tags.COOLED), slave=1)
            if rr.isError() or rc.isError():
                raise IOError("modbus read error")
            now = time.time()
            fresh = tags.decode(list(rr.registers), list(rc.bits), now)
            with _lock:
                STATE["tags"] = fresh
                STATE["plc_connected"] = True
                STATE["last_good_ts"] = now
            _historian_write(fresh)
        except Exception as e:
            # keep the last map; requality() ages it honestly on every read-out
            with _lock:
                STATE["plc_connected"] = False
            try:
                if client:
                    client.close()
            except Exception:
                pass
            client = None
            print(f"tagserver: poll failed ({e}); retrying", flush=True)
        time.sleep(max(0.0, POLL_S - (time.time() - t0)))


# ---------------------------------------------------------------- historian --
_DB = {"conn": None, "win_rows": 0, "win_t0": time.time()}
_db_lock = threading.Lock()     # the base poll and the fleet writer share one connection

_DDL = """
CREATE TABLE IF NOT EXISTS plant_tags (
  ts      TIMESTAMPTZ NOT NULL,
  tag     TEXT        NOT NULL,
  value   DOUBLE PRECISION,
  quality TEXT        NOT NULL
);
SELECT create_hypertable('plant_tags', 'ts', if_not_exists => TRUE);
"""


def _historian_write(fresh: dict):
    """Batch-insert this poll's GOOD tags. Connection is lazy + self-healing; TimescaleDB's
    create_hypertable runs once (plain-postgres fallback: the table still works, just unchunked)."""
    if psycopg2 is None:
        return
    with _db_lock:
        try:
            if _DB["conn"] is None:
                _DB["conn"] = psycopg2.connect(DSN)
                _DB["conn"].autocommit = True
                with _DB["conn"].cursor() as c:
                    try:
                        c.execute(_DDL)
                    except Exception:
                        c.execute(_DDL.split(";")[0])            # no timescale ext -> plain table
            rows = [(rec["ts"], tag, rec["value"], rec["quality"]) for tag, rec in fresh.items()]
            with _DB["conn"].cursor() as c:
                c.executemany(
                    "INSERT INTO plant_tags (ts, tag, value, quality) VALUES (to_timestamp(%s), %s, %s, %s)",
                    rows)
            _DB["win_rows"] += len(rows)
            now = time.time()
            with _lock:
                STATE["historian"]["connected"] = True
                STATE["historian"]["rows_total"] += len(rows)
                if now - _DB["win_t0"] >= 5.0:
                    STATE["historian"]["rows_per_s"] = round(_DB["win_rows"] / (now - _DB["win_t0"]), 1)
                    _DB["win_rows"], _DB["win_t0"] = 0, now
        except Exception as e:
            try:
                if _DB["conn"]:
                    _DB["conn"].close()
            except Exception:
                pass
            _DB["conn"] = None
            with _lock:
                STATE["historian"]["connected"] = False
            print(f"tagserver: historian write failed ({e}); will reconnect", flush=True)


# ---------------------------------------------------------------- fleet -----
_fleet_lock = threading.Lock()
FLEET: dict[str, dict] = {}      # PLC name -> registry entry
_HIST_Q: queue.Queue = queue.Queue(maxsize=600)
_HIST_DROPPED = {"batches": 0}


def _fleet_history(fresh: dict):
    """Queue one fleet poll for the historian. When the queue is full, drop the oldest batch
    and count it. The poll thread never waits for the database."""
    try:
        _HIST_Q.put_nowait(fresh)
        return
    except queue.Full:
        pass
    try:
        _HIST_Q.get_nowait()
    except queue.Empty:
        pass
    _HIST_DROPPED["batches"] += 1
    if _HIST_DROPPED["batches"] in (1, 10) or _HIST_DROPPED["batches"] % 100 == 0:
        print(f"tagserver: fleet historian queue full, dropped {_HIST_DROPPED['batches']} "
              f"batches so far", flush=True)
    try:
        _HIST_Q.put_nowait(fresh)
    except queue.Full:
        pass


def fleet_history_step(backoff_s: float = 5.0, timeout: float | None = None) -> bool:
    """Write one queued fleet batch to plant_tags. Return False when the queue stayed empty.

    After a failed write, wait backoff_s before the next try, so the base poll does not wait
    on the DB lock behind a dead connection. Queued rows keep their read timestamps."""
    try:
        batch = _HIST_Q.get(timeout=timeout)
    except queue.Empty:
        return False
    _historian_write(batch)
    with _lock:
        ok = STATE["historian"]["connected"]
    if not ok and psycopg2 is not None:
        time.sleep(backoff_s)
    return True


def fleet_history_loop():
    while True:
        fleet_history_step()


def _fleet_poll(entry: dict):
    """Poll one PLC until its stop event is set. Every stored value comes from a read."""
    drv, stop, table = entry["driver"], entry["stop"], entry["table"]
    name = entry["reg"]["name"]
    ok = None
    while not stop.is_set():
        t0 = time.time()
        try:
            image = drv.read_image()
            now = time.time()
            fresh = fleet.decode(table, image, now)
            if stop.is_set():
                break
            with _fleet_lock:
                entry["tags"] = fresh
                entry["connected"] = True
                entry["last_good_at"] = now
                if entry["first_good_at"] is None:
                    entry["first_good_at"] = now
                entry["poll_rtt_ms"] = round(drv.last_rtt_ms, 3) if drv.last_rtt_ms is not None else None
                entry["poll_error"] = None
            _fleet_history(fresh)
            if ok is not True:
                print(f"tagserver: fleet {name} polling ({entry['reg']['protocol']['kind']} "
                      f"rtt {entry['poll_rtt_ms']} ms)", flush=True)
                ok = True
        except Exception as e:
            # Keep the last map. requality() ages it on every read-out.
            with _fleet_lock:
                entry["connected"] = False
                entry["poll_error"] = str(e) or e.__class__.__name__
            if ok is not False:
                print(f"tagserver: fleet {name} poll failed ({e}), retrying every "
                      f"{FLEET_POLL_S} s", flush=True)
                ok = False
        stop.wait(max(0.0, FLEET_POLL_S - (time.time() - t0)))
    drv.close()


def fleet_enroll(body, token: str | None, now: float | None = None) -> tuple[int, dict]:
    """POST /enroll. Return (HTTP code, JSON body)."""
    if not ENROLL_KEY:
        return 403, {"enrolled": False, "error": "enrollment disabled: FLEET_ENROLL_KEY is empty"}
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not fleet.NAME_RE.match(name):
        return 400, {"enrolled": False, "error": "name must match the PLC name rule (FLEET.md 2)"}
    if not fleet.check_enroll_token(ENROLL_KEY, name, token):
        return 401, {"enrolled": False, "error": "X-Device-Token does not match"}
    try:
        reg = fleet.parse_enrollment(body)
        table = fleet.table_for(reg)
    except fleet.EnrollError as e:
        return 400, {"enrolled": False, "error": str(e)}
    key = fleet.registration_key(reg, table)
    now = time.time() if now is None else now
    old_entry = None
    with _fleet_lock:
        old_entry = FLEET.get(name)
        if old_entry is not None and old_entry["key"] == key:
            old_entry["reg"] = reg                       # a heartbeat: keep the thread
            old_entry["last_enroll_at"] = now
            return 200, {"enrolled": True, "tags": len(table), "poll": "kept"}
        entry = {
            "reg": reg, "table": table, "key": key, "driver": make_driver(reg["protocol"]),
            "stop": threading.Event(), "thread": None, "tags": {},
            "enrolled_at": now, "last_enroll_at": now, "first_good_at": None,
            "last_good_at": None, "poll_rtt_ms": None, "connected": False, "poll_error": None,
        }
        if old_entry is not None:
            old_entry["stop"].set()
        FLEET[name] = entry
        entry["thread"] = threading.Thread(target=_fleet_poll, args=(entry,), daemon=True,
                                           name=f"fleet-{name}")
        entry["thread"].start()
    verb = "restarted" if old_entry is not None else "started"
    print(f"tagserver: fleet {name} enrolled ({reg['profile']}, {len(table)} tags, poll {verb})",
          flush=True)
    return 200, {"enrolled": True, "tags": len(table), "poll": verb}


def _write_gate(token: str | None) -> tuple[int, dict] | None:
    if not WRITE_TOKEN:
        return 403, {"error": "writes disabled: SCADA_WRITE_TOKEN is empty"}
    if not fleet.check_write_token(WRITE_TOKEN, token):
        return 401, {"error": "X-Scada-Token does not match"}
    return None


def fleet_write(name: str, body, token: str | None) -> tuple[int, dict]:
    """POST /fleet/<name>/write. Write one setpoint over the PLC protocol."""
    denied = _write_gate(token)
    if denied:
        return denied[0], {"written": False, **denied[1]}
    with _fleet_lock:
        entry = FLEET.get(name)
    if entry is None:
        return 404, {"written": False, "error": f"{name} is not enrolled"}
    if not isinstance(body, dict) or "tag" not in body or "value" not in body:
        return 400, {"written": False, "error": "body must be {tag, value}"}
    tag, value = body["tag"], body["value"]
    try:
        index, raw = fleet.write_plan(entry["table"], tag, value)
    except fleet.WriteDenied as e:
        return 403, {"written": False, "error": str(e)}
    except ValueError as e:
        return 400, {"written": False, "error": str(e)}
    if entry["stop"].is_set():                          # deleted or re-enrolled meanwhile
        return 404, {"written": False, "error": f"{name} is not enrolled"}
    t0 = time.perf_counter()
    try:
        entry["driver"].write_mw(index, raw)
    except Exception as e:
        return 502, {"written": False, "error": f"protocol write failed: {e}"}
    finally:
        if entry["stop"].is_set():                      # do not leave a stopped driver connected
            entry["driver"].close()
    ack_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    print(f"tagserver: fleet write {name} {tag} = {value} (%MW{index} raw {raw}, ack {ack_ms} ms)",
          flush=True)
    return 200, {"written": True, "plc": name, "tag": tag, "value": value,
                 "address": f"%MW{index}", "raw": raw, "ack_ms": ack_ms}


def fleet_delete(name: str, token: str | None) -> tuple[int, dict]:
    """DELETE /fleet/<name>. Stop the poll and remove the tags."""
    denied = _write_gate(token)
    if denied:
        return denied[0], {"deleted": False, **denied[1]}
    with _fleet_lock:
        entry = FLEET.pop(name, None)
    if entry is None:
        return 404, {"deleted": False, "error": f"{name} is not enrolled"}
    entry["stop"].set()
    print(f"tagserver: fleet {name} removed", flush=True)
    return 200, {"deleted": True, "name": name}


def _fleet_snapshots() -> list[dict]:
    now = time.time()
    with _fleet_lock:
        entries = list(FLEET.values())
        return [{
            "reg": e["reg"], "table": e["table"],
            "aged": fleet.requality(e["tags"], now, STALE_S, BAD_S),
            "enrolled_at": e["enrolled_at"], "last_enroll_at": e["last_enroll_at"],
            "first_good_at": e["first_good_at"], "last_good_at": e["last_good_at"],
            "poll_rtt_ms": e["poll_rtt_ms"], "connected": e["connected"],
            "poll_error": e["poll_error"],
        } for e in entries]


# ---------------------------------------------------------------- chaos -----
# PS6 (SCENARIOS 2.7): the monitor runs out of memory. A thread appends real written bytes
# to an in-memory buffer, so the working set climbs toward the pod limit until an OOM kill.
# The flag lives in memory only, so the kill clears it and the pod restarts clean. Every POST
# is gated by X-Scada-Token, so an empty token fails closed (403).
_MIB = 1024 * 1024
_CHAOS_TICK_S = 0.25
_CHAOS_MIN, _CHAOS_MAX = 0.05, 8.0
_chaos_lock = threading.Lock()
_CHAOS = {
    "active": False,
    "mib_per_s": 0.5,
    "leaked_bytes": 0,
    "started_at": None,
    "buf": [],          # the leaked memory, held so it stays resident
    "stop": None,       # the running leak's stop event, or None
    "thread": None,
}


def _chaos_loop(stop: threading.Event, mib_per_s: float):
    """Append real bytes every tick until the stop event fires (or an OOM kill ends it)."""
    per_tick = max(1, int(round(mib_per_s * _MIB * _CHAOS_TICK_S)))
    while not stop.is_set():
        chunk = b"\x01" * per_tick                       # written, so the pages stay resident
        with _chaos_lock:
            if _CHAOS["stop"] is not stop:               # a reset or a newer leak replaced us
                return
            _CHAOS["buf"].append(chunk)
            _CHAOS["leaked_bytes"] += len(chunk)
        stop.wait(_CHAOS_TICK_S)


def _chaos_view() -> dict:
    return {
        "active": _CHAOS["active"],
        "leaked_mib": round(_CHAOS["leaked_bytes"] / _MIB, 3),
        "mib_per_s": _CHAOS["mib_per_s"],
        "started_at": _CHAOS["started_at"],
    }


def chaos_state() -> dict:
    """GET /chaos body."""
    with _chaos_lock:
        return _chaos_view()


def chaos_leak(body, token: str | None) -> tuple[int, dict]:
    """POST /chaos/leak. Start (or restart) the leak thread at mib_per_s, clamped to the band."""
    denied = _write_gate(token)
    if denied:
        return denied[0], {"active": _CHAOS["active"], **denied[1]}
    rate = 0.5
    if isinstance(body, dict) and body.get("mib_per_s") is not None:
        try:
            rate = float(body["mib_per_s"])
        except (TypeError, ValueError):
            return 400, {"active": _CHAOS["active"], "error": "mib_per_s must be a number"}
    rate = max(_CHAOS_MIN, min(_CHAOS_MAX, rate))
    with _chaos_lock:
        old = _CHAOS["stop"]
        if old is not None:
            old.set()                                    # stop any leak already running
        stop = threading.Event()
        _CHAOS.update(active=True, mib_per_s=rate, leaked_bytes=0,
                      started_at=time.time(), buf=[], stop=stop)
        t = threading.Thread(target=_chaos_loop, args=(stop, rate), daemon=True, name="chaos-leak")
        _CHAOS["thread"] = t
        t.start()
        view = _chaos_view()
    print(f"tagserver: chaos leak started at {rate} MiB/s (PS6)", flush=True)
    return 200, view


def chaos_reset(token: str | None) -> tuple[int, dict]:
    """POST /chaos/reset. Stop the thread and free the buffer."""
    denied = _write_gate(token)
    if denied:
        return denied[0], {"active": _CHAOS["active"], **denied[1]}
    with _chaos_lock:
        stop = _CHAOS["stop"]
        if stop is not None:
            stop.set()
        _CHAOS.update(active=False, leaked_bytes=0, started_at=None, buf=[], stop=None, thread=None)
        view = _chaos_view()
    print("tagserver: chaos leak reset (PS6)", flush=True)
    return 200, {"reset": True, **view}


# --------------------------------------------------------------------- HTTP --
def _snapshot():
    now = time.time()
    with _lock:
        aged = tags.requality(STATE["tags"], now, STALE_S, BAD_S)
        return aged, dict(STATE["historian"]), STATE["plc_connected"]


_WRITE_RE = re.compile(r"^/fleet/([^/]+)/write$")
_PLC_RE = re.compile(r"^/fleet/([^/]+)$")


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain", headers=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj, headers=None):
        return self._send(code, json.dumps(obj), "application/json", headers)

    def _body(self):
        """The JSON request body, or raise ValueError."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad Content-Length")
        if length > MAX_BODY:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            return json.loads(raw or b"null")
        except Exception:
            raise ValueError("body is not valid JSON")

    def do_GET(self):
        if self.path == "/metrics":
            aged, hist, plc = _snapshot()
            extra = [
                f'scada_plc_connected{{namespace="plant",pod="tag-server"}} {1 if plc else 0}',
                f'scada_historian_connected{{namespace="plant",pod="tag-server"}} {1 if hist["connected"] else 0}',
                f'scada_historian_rows_total{{namespace="plant",pod="tag-server"}} {hist["rows_total"]}',
            ]
            return self._send(200, tags.prom_text(aged, extra))
        if self.path == "/tags":
            aged, hist, plc = _snapshot()
            hist = {**hist, "queue_depth": _HIST_Q.qsize(), "queue_max": _HIST_Q.maxsize,
                    "dropped_batches": _HIST_DROPPED["batches"]}
            rows = []
            for tag, meta in BY_TAG.items():
                rec = aged.get(tag)
                rows.append({
                    "tag": tag, "asset": meta["asset"], "signal": meta["signal"],
                    "unit": meta["unit"], "kind": meta["kind"], "address": meta["address"],
                    "value": rec["value"] if rec else None,
                    "quality": rec["quality"] if rec else "BAD",
                    "ts": rec["ts"] if rec else None,
                })
            return self._send(200, json.dumps({
                "source": "scada", "plc_connected": plc, "historian": hist, "tags": rows,
            }), "application/json")
        if self.path == "/healthz":
            return self._send(200, "ok\n")
        if self.path == "/chaos":
            return self._json(200, chaos_state())
        path = urlsplit(self.path).path
        if path == "/fleet":
            return self._json(200, fleet.fleet_json(_fleet_snapshots()),
                              {"X-Fleet-Enroll": "enabled" if ENROLL_KEY else "disabled"})
        if path == "/domains":
            return self._json(200, fleet.domains([s["reg"] for s in _fleet_snapshots()]))
        if path == "/metrics/fleet":
            return self._send(200, fleet.prom_text(_fleet_snapshots()))
        self._send(404, "not found\n")

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/chaos/reset":
            code, out = chaos_reset(self.headers.get("X-Scada-Token"))
            return self._json(code, out)
        m = _WRITE_RE.match(path)
        if path not in ("/enroll", "/chaos/leak") and not m:
            return self._send(404, "not found\n")
        try:
            body = self._body()
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        if path == "/enroll":
            code, out = fleet_enroll(body, self.headers.get("X-Device-Token"))
        elif path == "/chaos/leak":
            code, out = chaos_leak(body, self.headers.get("X-Scada-Token"))
        else:
            code, out = fleet_write(unquote(m.group(1)), body, self.headers.get("X-Scada-Token"))
        return self._json(code, out)

    def do_DELETE(self):
        m = _PLC_RE.match(urlsplit(self.path).path)
        if not m:
            return self._send(404, "not found\n")
        code, out = fleet_delete(unquote(m.group(1)), self.headers.get("X-Scada-Token"))
        return self._json(code, out)

    def log_message(self, *a):                                # quiet access log
        pass


def main():
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=fleet_history_loop, daemon=True).start()
    print(f"tag-server up on :{PORT} | {len(TABLE)} tags ({sum(1 for r in TABLE if r['kind']=='measured')} "
          f"measured, {sum(1 for r in TABLE if r['kind']=='derived')} derived) | plc {PLC_HOST}:{PLC_PORT} "
          f"MW@{MW_BASE} | poll {POLL_S}s", flush=True)
    print(f"tag-server fleet: enrollment {'enabled' if ENROLL_KEY else 'DISABLED (FLEET_ENROLL_KEY empty)'} | "
          f"writes {'enabled' if WRITE_TOKEN else 'DISABLED (SCADA_WRITE_TOKEN empty)'} | "
          f"poll {FLEET_POLL_S}s", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
