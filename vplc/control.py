"""Control HTTP of the virtual PLC (FLEET.md section 6).

  GET  /healthz   200 "ok"
  GET  /state     {name, profile, state, task, scan, started_at, enrollment, fault, denied_writes}
  PUT  /task      body {"st": "...", "manifest": {...}}. Compile first, then swap at a scan boundary.
                  400 {line, col, error} on a compile error. The old task keeps running.
  POST /run       enter RUN (409 when no task is loaded)
  POST /stop      enter STOP

PUT /task, POST /run, and POST /stop need header X-Device-Token equal to DEVICE_TOKEN. An empty
DEVICE_TOKEN turns those endpoints off (403), so a PLC without a token fails closed.
"""
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tasklib
from runtime import NoTaskError
from st import CompileError, compile_task

MAX_BODY = 1024 * 1024


class ControlServer:
    def __init__(self, runtime, enroller, token, host="0.0.0.0", port=8080):
        self.rt = runtime
        self.enroller = enroller
        self.token = token or ""
        self.httpd = ThreadingHTTPServer((host, port), _make_handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="control", daemon=True)
        self._thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread is not None:
            self._thread.join(5.0)
            self._thread = None

    # ------------------------------------------------------------------ actions --
    def state(self):
        body = self.rt.status()
        body["enrollment"] = self.enroller.status() if self.enroller else {"ok": None, "ts": None, "error": None}
        return body

    def check_token(self, headers):
        """Return None when the token is good, else (code, body)."""
        if not self.token:
            return 403, {"error": "DEVICE_TOKEN is not set, control writes are off"}
        given = headers.get("X-Device-Token") or ""
        if not hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8")):
            return 401, {"error": "bad or missing X-Device-Token"}
        return None

    def put_task(self, raw):
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            return 400, {"line": None, "col": None, "error": f"the body is not valid JSON: {e}"}
        if not isinstance(body, dict) or not isinstance(body.get("st"), str):
            return 400, {"line": None, "col": None, "error": "the body needs a string field 'st'"}
        try:
            manifest = tasklib.validate_manifest(body.get("manifest") or {})
        except tasklib.ManifestError as e:
            return 400, {"line": None, "col": None, "error": str(e)}
        try:
            compiled = compile_task(body["st"])
        except CompileError as e:
            return 400, e.as_dict()
        self.rt.load(compiled, manifest)
        return 200, {"loaded": True, "state": self.rt.state, "task": self.rt.task.info()}

    def run(self):
        try:
            self.rt.run()
        except NoTaskError as e:
            return 409, {"error": str(e)}
        return 200, {"state": self.rt.state}

    def stop_plc(self):
        self.rt.stop()
        return 200, {"state": self.rt.state}


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vplc/0.1"

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _path(self):
            return self.path.split("?", 1)[0].rstrip("/") or "/"

        def _read_body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._send(400, {"line": None, "col": None, "error": "bad Content-Length"})
                return None
            if length > MAX_BODY:
                self._send(413, {"error": f"the body is larger than {MAX_BODY} bytes"})
                return None
            return self.rfile.read(length)

        def do_GET(self):
            path = self._path()
            if path == "/healthz":
                return self._send(200, b"ok\n", "text/plain")
            if path == "/state":
                return self._send(200, app.state())
            self._send(404, {"error": "not found"})

        def do_PUT(self):
            if self._path() != "/task":
                return self._send(404, {"error": "not found"})
            denied = app.check_token(self.headers)
            if denied:
                return self._send(*denied)
            raw = self._read_body()
            if raw is None:
                return
            self._send(*app.put_task(raw))

        def do_POST(self):
            path = self._path()
            if path not in ("/run", "/stop"):
                return self._send(404, {"error": "not found"})
            denied = app.check_token(self.headers)
            if denied:
                return self._send(*denied)
            self._send(*(app.run() if path == "/run" else app.stop_plc()))

        def log_message(self, *a):                            # quiet access log
            pass

    return Handler
