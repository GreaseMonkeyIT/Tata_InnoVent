"""2F.2 SCADA tag server — the industrial data path made real (master plan 2F, LOG-033 decisions).

physics (plant-sim) → OpenPLC (%MW words / trip coils, real Modbus TCP) → THIS SERVICE →
  · tag DB (tags.py: ISA-style names, units, PLC addresses, GOOD/STALE/BAD quality)
  · historian (TimescaleDB hypertable on the 64Gi slowdisk — closes the PIVOT_SETUP §7 line)
  · /metrics (identical series names/labels to plant-sim's exposition → the aggregator repoint
    at cutover is ONE queries.yaml-URL-shaped swap: apply the scada ServiceMonitor, delete the
    plant-sim one; the sim's /metrics stays as an unscraped debug tap)
  · /tags (the UI's tag browser: values + quality + addresses + historian rate)

Honesty rules: the tag server READS ONLY (FC03 holding block + FC01 coils — it never writes
the PLC; the sim is the field wiring, the trip program is the authority). A failed poll ages
quality (STALE→BAD) instead of repeating values as fresh; BAD tags leave /metrics entirely.
Historian down → serving continues, rows just stop counting (retry forever, like the sim's
PLC loop). Env: PLC_HOST/PLC_PORT/PLC_MW_BASE · HISTORIAN_DSN · POLL_S/STALE_S/BAD_S · PORT.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import tags

PLC_HOST = os.environ.get("PLC_HOST", "openplc.plant.svc")
PLC_PORT = int(os.environ.get("PLC_PORT", "502"))
MW_BASE = int(os.environ.get("PLC_MW_BASE", "1024"))
POLL_S = float(os.environ.get("POLL_S", "1.0"))
STALE_S = float(os.environ.get("STALE_S", "10"))
BAD_S = float(os.environ.get("BAD_S", "30"))
PORT = int(os.environ.get("PORT", "9300"))
DSN = os.environ.get("HISTORIAN_DSN",
                     "host=historian-db.plant.svc dbname=postgres user=postgres password=plant-historian")

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


# --------------------------------------------------------------------- HTTP --
def _snapshot():
    now = time.time()
    with _lock:
        aged = tags.requality(STATE["tags"], now, STALE_S, BAD_S)
        return aged, dict(STATE["historian"]), STATE["plc_connected"]


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

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
        self._send(404, "not found\n")

    def log_message(self, *a):                                # quiet access log
        pass


def main():
    threading.Thread(target=poll_loop, daemon=True).start()
    print(f"tag-server up on :{PORT} | {len(TABLE)} tags ({sum(1 for r in TABLE if r['kind']=='measured')} "
          f"measured, {sum(1 for r in TABLE if r['kind']=='derived')} derived) | plc {PLC_HOST}:{PLC_PORT} "
          f"MW@{MW_BASE} | poll {POLL_S}s", flush=True)
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
