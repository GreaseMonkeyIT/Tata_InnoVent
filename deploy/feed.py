#!/usr/bin/env python3
"""feed.py: a read-only SCADA feed page for the demo video (POC_SCRIPT.md section 2, LOG-096).

Run it on the box (no sudo), then reach it from the laptop through an SSH tunnel:
    screen -dmS visr-feed python3 ~/Tata_InnoVent/deploy/feed.py
    ssh -N -L 8765:127.0.0.1:8765 forge          # on the laptop, then open http://localhost:8765

Once a second it prints one line from the SCADA tag server: press-1 current, temperature, and
DERATE_PCT from plc-stamping with their quality, and the rail psu-a voltage. It also prints every new
ledger row: a fault-shell fire or reset, an Execute with its write, a measured relief. It listens on
127.0.0.1 only, it has no command endpoint, and it only reads (GET) from the tag server and the api.
Every line also goes to /var/tmp/visr-feed-<start time>.log with its epoch time, for the video edit.
Env: FEED_PORT (8765), FEED_PLC (plc-stamping), FEED_ASSET (PRESS_1), FEED_RAIL (PSU_A).
"""
import json
import os
import queue
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("FEED_PORT", "8765"))
PLC = os.environ.get("FEED_PLC", "plc-stamping")
ASSET = os.environ.get("FEED_ASSET", "PRESS_1")
RAIL = os.environ.get("FEED_RAIL", "PSU_A")
LOG = "/var/tmp/visr-feed-%s.log" % time.strftime("%Y%m%d-%H%M%S")
KUBECONFIG = os.path.expanduser("~/.kube/config")
LINES: list = []                     # the last 300 lines, for a page that opens late
SUBS: list = []                      # one queue per open page
LOCK = threading.Lock()


def cluster_ip(ns, svc):
    out = subprocess.run(["kubectl", "-n", ns, "get", "svc", svc, "-o", "jsonpath={.spec.clusterIP}"],
                         capture_output=True, text=True, env={**os.environ, "KUBECONFIG": KUBECONFIG})
    return out.stdout.strip()


def get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.load(r)


def emit(kind, text):
    now = time.time()
    line = {"t": time.strftime("%H:%M:%S", time.localtime(now)), "kind": kind, "text": text}
    with open(LOG, "a") as f:
        f.write("%.3f %s %s %s\n" % (now, line["t"], kind, text))
    with LOCK:
        LINES.append(line)
        del LINES[:-300]
        for q in SUBS:
            q.put(line)


def q_mark(tag):
    return "" if not tag else ("" if tag.get("quality") == "GOOD" else " " + tag.get("quality", "?"))


def poll():
    ts, api = cluster_ip("plant", "tag-server"), cluster_ip("aiops", "api")
    emit("info", "read-only feed: tag server %s:9300, ledger %s:8088" % (ts, api))
    seen = time.time()
    while True:
        t0 = time.time()
        try:
            fleet = {p["name"]: p for p in get("http://%s:9300/fleet" % ts)}
            tags = {t["tag"]: t for t in (fleet.get(PLC) or {}).get("tags") or []}
            base = {t["tag"]: t for t in get("http://%s:9300/tags" % ts).get("tags") or []}
            pre = "FLEET.%s.%s." % (PLC.upper().replace("-", "_"), ASSET)
            a, tc, d = tags.get(pre + "AMPS"), tags.get(pre + "TEMP"), tags.get(pre + "DERATE_PCT")
            v = base.get("PLANT.%s.VOLTS" % RAIL)
            emit("scada", "%s.AMPS %6.1f A%s | TEMP %5.1f C%s | DERATE %3.0f %%%s | %s %6.1f V" % (
                ASSET, (a or {}).get("value") or 0, q_mark(a), (tc or {}).get("value") or 0, q_mark(tc),
                (d or {}).get("value") or 0, q_mark(d), RAIL.lower().replace("_", "-"), (v or {}).get("value") or 0))
        except Exception as e:
            emit("blind", "tag server does not answer (%s)" % e.__class__.__name__)
        try:
            rows = get("http://%s:8088/api/audit" % api).get("entries") or []
            for r in rows:
                if (r.get("ts") or 0) > seen:
                    seen = r["ts"]
                    ev = r.get("evidence") or {}
                    extra = ""
                    if r.get("verb") == "execute":
                        extra = " -> %s = %s (via %s, ack %s ms)" % (ev.get("tag"), ev.get("to"), ev.get("plc"), ev.get("ack_ms"))
                    elif r.get("verb") == "relief":
                        extra = " %s -> %s A, rail %s -> %s V" % (ev.get("amps_before"), ev.get("amps_after"),
                                                               ev.get("volts_before"), ev.get("volts_after"))
                    emit("ledger", ">> %s %s %s %s%s" % (r.get("actor"), r.get("verb"), r.get("target"),
                                                          r.get("status"), extra))
        except Exception:
            pass
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>VISR SCADA feed</title><style>
:root{--bg:#0a0d14;--text:#d2d6e4;--weak:rgba(210,214,228,.55);--teal:#12c6b3;--amber:#ff9c00;--red:#f2495c;--blue:#aeb2ff}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 ui-monospace,"Cascadia Code",Consolas,monospace}
header{position:sticky;top:0;padding:8px 12px;background:#11151f;border-bottom:1px solid rgba(150,165,210,.14);
letter-spacing:2px;text-transform:uppercase;font-size:12px;color:var(--weak)}header b{color:var(--teal)}
#l{padding:8px 12px;white-space:pre}.t{color:var(--weak)}.ledger{color:var(--blue)}.blind{color:var(--amber)}.info{color:var(--weak)}
</style></head><body><header><b>VISR</b> &middot; SCADA feed &middot; read-only &middot; tag server + ledger</header><div id="l"></div>
<script>const l=document.getElementById('l');function add(x){const d=document.createElement('div');d.className=x.kind;
d.innerHTML='<span class="t">'+x.t+'</span>  '+x.text.replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
l.appendChild(d);while(l.children.length>300)l.removeChild(l.firstChild);window.scrollTo(0,document.body.scrollHeight);}
const es=new EventSource('/events');es.onmessage=e=>add(JSON.parse(e.data));</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = queue.Queue()
            with LOCK:
                backlog = list(LINES)
                SUBS.append(q)
            try:
                for line in backlog:
                    self.wfile.write(("data: %s\n\n" % json.dumps(line)).encode())
                self.wfile.flush()
                while True:
                    line = q.get()
                    self.wfile.write(("data: %s\n\n" % json.dumps(line)).encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with LOCK:
                    SUBS.remove(q)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    print("feed on http://127.0.0.1:%d, log %s" % (PORT, LOG), flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
