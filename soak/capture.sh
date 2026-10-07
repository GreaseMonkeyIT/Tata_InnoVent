#!/usr/bin/env bash
# SiliconKnights: capture one scenario fire with the full engine answer, for slide evidence.
#
# soak.sh keeps only a summary row per sample. This script keeps the full JSON. It fires one scenario
# through the same console route (POST /api/scenarios/<id>/trigger), polls /api/graph every POLL_S
# seconds, and writes every answer to graphs.jsonl. It also saves:
#   graph_detected.json   the first graph with an integrity finding, a root, or a card
#   narrative_detected.json  /api/narrative at that moment
#   plant_detected.json   /api/plant at that moment
#   tags_detected.json    /api/tags at that moment
#   markers.json          the epochs of the baseline start, the fire, the detection, the reset and the end
# It changes the plant, so the operator starts it. Never run it at the same time as another fire.
#
# Run on the BOX (where kubectl talks to the cluster). Requires: bash, kubectl, python3.
#
#   bash soak/capture.sh                       # Scenario 4B (default)
#   SCENARIO=PS1 OBSERVE_S=240 bash soak/capture.sh
#   screen -dmS visr-cap4b bash -c 'bash ~/Tata_InnoVent/soak/capture.sh > /var/tmp/visr-cap4b.log 2>&1'
#
# Evidence goes to $OUT (default /var/tmp/visr-capture-<id>-<time>). Scenario 4A and Scenario 6 need
# rogue-ews or a lone run: use soak.sh for them.
set -uo pipefail
export KUBECONFIG=${KUBECONFIG:-$HOME/.kube/config}
SCENARIO=$(echo "${SCENARIO:-PS4B}" | tr '[:lower:]' '[:upper:]')
BASELINE_S=${BASELINE_S:-60}
OBSERVE_S=${OBSERVE_S:-180}
COOLDOWN_S=${COOLDOWN_S:-150}
POLL_S=${POLL_S:-3}
case "$SCENARIO" in PS4A|PS6) echo "STOP $SCENARIO needs soak.sh (rogue-ews or a lone run)"; exit 1 ;; esac
API=${API:-http://$(kubectl -n aiops get svc api -o jsonpath='{.spec.clusterIP}'):8088}
OUT=${OUT:-/var/tmp/visr-capture-$(echo "$SCENARIO" | tr '[:upper:]' '[:lower:]')-$(date +%Y%m%d-%H%M%S)}
VISR_TOKEN=$(kubectl -n aiops get secret visr-auth -o jsonpath='{.data.operator-token}' | base64 -d)
[ -n "$VISR_TOKEN" ] || { echo "STOP the visr-auth Secret has no operator token"; exit 1; }
mkdir -p "$OUT"
export API OUT VISR_TOKEN SCENARIO BASELINE_S OBSERVE_S COOLDOWN_S POLL_S

python3 - <<'PY'
import json, os, sys, time, urllib.error, urllib.request

API, OUT, TOKEN, SID = os.environ["API"], os.environ["OUT"], os.environ["VISR_TOKEN"], os.environ["SCENARIO"]
BASE, OBS, COOL, POLL = (int(os.environ[k]) for k in ("BASELINE_S", "OBSERVE_S", "COOLDOWN_S", "POLL_S"))
marks = {"scenario": SID, "api": API}

def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)

def call(method, path, auth=False):
    req = urllib.request.Request(API + path, method=method)
    if auth:
        req.add_header("X-Auth-Token", TOKEN)
        req.add_header("X-Remote-User", "capture")
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read()
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None

def save(name, obj):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2)

def detected(g):
    return bool(g) and bool(g.get("integrity") or g.get("root") or g.get("incipient"))

def window(phase, secs, out):
    end = time.time() + secs
    while time.time() < end:
        t = time.time()
        g = call("GET", "/api/graph")[1]
        out.write(json.dumps({"epoch": round(t, 2), "phase": phase, "graph": g}) + "\n")
        out.flush()
        if phase == "fire" and "detect_epoch" not in marks and detected(g):
            marks["detect_epoch"] = round(t, 2)
            marks["detect_s"] = round(t - marks["fire_epoch"], 1)
            save("graph_detected.json", g)
            for ep, name in (("/api/narrative", "narrative"), ("/api/plant", "plant"), ("/api/tags", "tags")):
                save(name + "_detected.json", call("GET", ep)[1])
            log("DETECTED at +%.1f s" % marks["detect_s"])
        time.sleep(max(0.0, POLL - (time.time() - t)))

st, health = call("GET", "/api/health")
if not health or health.get("auth") != "enforced":
    log("STOP the api does not answer with auth enforced"); sys.exit(1)
g0 = call("GET", "/api/graph")[1] or {}
if detected(g0):
    log("WARN the graph is not quiet before the baseline (root %r)" % g0.get("root"))
log("capture %s -> %s (baseline %ds, observe %ds, cooldown %ds, poll %ds)" % (SID, OUT, BASE, OBS, COOL, POLL))
with open(os.path.join(OUT, "graphs.jsonl"), "w") as out:
    marks["baseline_epoch"] = round(time.time(), 2)
    window("baseline", BASE, out)
    marks["fire_epoch"] = round(time.time(), 2)
    st, body = call("POST", "/api/scenarios/%s/trigger" % SID, auth=True)
    marks["fire_status"] = st
    log("FIRE %s -> HTTP %s" % (SID, st))
    if not 200 <= st < 300:
        save("markers.json", marks); log("STOP the trigger failed"); sys.exit(1)
    window("fire", OBS, out)
    marks["reset_epoch"] = round(time.time(), 2)
    st, _ = call("POST", "/api/scenarios/%s/reset" % SID, auth=True)
    marks["reset_status"] = st
    log("RESET %s -> HTTP %s" % (SID, st))
    window("cooldown", COOL, out)
marks["end_epoch"] = round(time.time(), 2)
save("markers.json", marks)
log("DONE %s detect_s=%s. Evidence: %s" % (SID, marks.get("detect_s"), OUT))
PY
