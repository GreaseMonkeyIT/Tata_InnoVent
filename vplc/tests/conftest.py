"""Shared test helpers. Tests bind to free high ports on 127.0.0.1, never to 102 or 502."""
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from image import Image
from runtime import Runtime
from st import compile_task

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Clock:
    """An injected millisecond clock."""

    def __init__(self, ms=0):
        self.ms = ms

    def __call__(self):
        return self.ms

    def advance(self, ms):
        self.ms += ms
        return self.ms


class Prog:
    """Compile a PROGRAM and scan it on a bare image with an injected clock."""

    def __init__(self, source, reserve_system_words=True):
        self.task = compile_task(source, reserve_system_words=reserve_system_words)
        self.img = Image()
        self.task.apply_initial_values(self.img)
        self.inst = self.task.instantiate(self.img)
        self.now = 0

    def scan(self, advance_ms=0, times=1):
        for _ in range(times):
            self.now += advance_ms
            self.inst.scan(self.now)
        return self

    def __getitem__(self, name):
        return self.inst.get(name)


def program(decls, body):
    return f"PROGRAM t\n  VAR\n{decls}\n  END_VAR\n{body}\nEND_PROGRAM\n"


def make_runtime(source=None, manifest=None, clock=None, **kw):
    rt = Runtime("plc-test", kw.pop("profile", "generic-iec"), clock_ms=clock or Clock(), **kw)
    if source is not None:
        rt.load(compile_task(source), manifest or {})
    return rt


class CaptureServer:
    """A local HTTP server that records POST bodies and headers and answers a fixed code."""

    def __init__(self, code=200):
        self.code = code
        self.requests = []
        self.event = threading.Event()
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                outer.requests.append({"path": self.path, "headers": dict(self.headers.items()),
                                       "body": json.loads(raw.decode("utf-8"))})
                body = json.dumps({"enrolled": outer.code == 200}).encode()
                self.send_response(outer.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                outer.event.set()

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/enroll"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def wait_for(self, count, timeout=10.0):
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.requests) >= count:
                return True
            self.event.wait(0.05)
            self.event.clear()
        return len(self.requests) >= count

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def capture():
    srv = CaptureServer()
    yield srv
    srv.close()
