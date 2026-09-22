"""SCADA enrollment client (FLEET.md section 6).

The client posts to ENROLL_URL with header X-Device-Token:
  - after the first RUN
  - after every task load
  - every 30 s as a heartbeat, once one of the above has happened

A failure is recorded in status (and so in /state). It never stops the PLC.
"""
import json
import threading
import time
import urllib.error
import urllib.request

import tasklib
from profiles import protocol_block
from runtime import VERSION

HEARTBEAT_S = 30.0
TIMEOUT_S = 5.0

# The tag server is an in-cluster service. Never route enrollment through an HTTP proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Enroller:
    def __init__(self, runtime, url, token, host, scada_port, heartbeat_s=HEARTBEAT_S, timeout_s=TIMEOUT_S,
                 wall=None):
        self.rt = runtime
        self.url = url or ""
        self.token = token or ""
        self.host = host
        self.scada_port = scada_port
        self.heartbeat_s = heartbeat_s
        self.timeout_s = timeout_s
        self.wall = wall or time.time
        self._lock = threading.Lock()
        self._status = {"ok": None, "ts": None, "error": None}
        self._kick = threading.Event()
        self._halt = threading.Event()
        self._active = False
        self._thread = None
        self.attempts = 0
        if not self.url:
            self._status["error"] = "ENROLL_URL is empty, enrollment is off"

    def status(self):
        with self._lock:
            return dict(self._status)

    def body(self):
        rt = self.rt
        task = rt.task
        manifest = task.manifest if task else {}
        return {
            "name": rt.name,
            "profile": rt.profile,
            "protocol": protocol_block(rt.profile, self.host, self.scada_port),
            "task": task.info() if task else None,
            "cell": tasklib.cell_summary(manifest),
            "io_extra": list(manifest.get("io_extra") or []),
            "runtime": {"version": VERSION, "started_at": rt.started_at},
        }

    def kick(self, kind=None):
        """Ask for an enrollment now. The runtime calls this on first RUN and on every load."""
        self._kick.set()

    def enroll_once(self):
        """Post one enrollment. Record the result. Return True on a 2xx answer."""
        if not self.url:
            return False
        data = json.dumps(self.body()).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST", headers={
            "Content-Type": "application/json", "X-Device-Token": self.token})
        ok, error = False, None
        try:
            with _OPENER.open(req, timeout=self.timeout_s) as resp:
                resp.read(65536)
                ok = 200 <= resp.status < 300
                if not ok:
                    error = f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(200).decode("utf-8", "replace").strip()
            except Exception:
                pass
            error = f"HTTP {e.code}" + (f": {detail}" if detail else "")
        except urllib.error.URLError as e:
            error = f"cannot reach {self.url}: {e.reason}"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        with self._lock:
            self.attempts += 1
            self._status = {"ok": ok, "ts": self.wall(), "error": error}
        if not ok:
            print(f"vplc {self.rt.name}: enrollment failed ({error}), will retry", flush=True)
        return ok

    def start(self):
        self._halt.clear()
        self._thread = threading.Thread(target=self._loop, name="enroll", daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0):
        self._halt.set()
        self._kick.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _loop(self):
        while not self._halt.is_set():
            kicked = self._kick.wait(self.heartbeat_s if self._active else 1.0)
            if self._halt.is_set():
                break
            if kicked:
                self._kick.clear()
                self._active = True
            elif not self._active:
                continue
            try:
                self.enroll_once()
            except Exception as e:                                  # never fatal
                print(f"vplc {self.rt.name}: enrollment error {e}", flush=True)
