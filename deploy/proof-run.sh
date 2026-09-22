#!/usr/bin/env bash
# proof-run.sh: one timed run of the demo set on the box, with every answer saved as evidence.
# Run it after the PS0 soak passes. It fires faults, so it ends the quiet soak.
#
#   screen -dmS visr-proof bash -c 'bash ~/Tata_InnoVent/deploy/proof-run.sh > /var/tmp/visr-proof.log 2>&1'
#
# Steps:
#   0. Preflight: the api answers with auth enforced, and the last 30 PS0 watcher lines are QUIET.
#      FORCE=1 skips the soak check. The watcher log is copied to $OUT as the soak evidence, and
#      comment lines in it mark the start and the end of this run. The watcher keeps running, so
#      its log also shows the return to QUIET after the run.
#   1. Refusals: a fire without a token must get 401. An execute with a stale id must get 409.
#   2. PS1: wait for a compressor OFF window, fire PS1, and time the verdict (root press-1).
#   3. Execute: confirm the derate proposal, wait for the relief row, then restore press-1.
#   4. Reset PS1 and time how long the verdict takes to clear.
#   5. PS5: wait for a calm verdict, fire PS5, and record the first card and the trip of each machine.
#      The lead time compares a machine's own card with its own trip. Reset PS5.
#   5b. PS2, PS3, PS4A, PS4B (SCENARIOS.md): fire each, time its evidence, reset, time the clear.
#       PS6 last: time the leak card, the blind SCADA view after the OOM kill, and the recovery.
#   6. Save the audit rows and the chain check. Write summary.json and summary.md.
# It takes about 60 to 75 minutes.
#
# Evidence goes to $OUT (default /var/tmp/visr-proof-<time>). Every file is the raw JSON the api returned.
# Each fault also writes <id>_timeline.json: one compact row per poll (root, findings, cards, integrity
# kinds, loop hops, tripped machines). A check that fails saves its last graph as <id>_graph_timeout.json.
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
API=${API:-http://127.0.0.1:30088}
WATCH_LOG=${WATCH_LOG:-/var/tmp/visr-ps0-soak.log}
OUT=${OUT:-/var/tmp/visr-proof-$(date +%Y%m%d-%H%M%S)}
FORCE=${FORCE:-0}
mkdir -p "$OUT"
die() { echo "STOP $*"; exit 1; }

curl -s -m 8 "$API/api/health" | grep -q '"auth":"enforced"' || die "the api does not answer with auth enforced"
if [ "$FORCE" != 1 ]; then
  [ -f "$WATCH_LOG" ] || die "no PS0 watcher log at $WATCH_LOG. Set FORCE=1 to run without the soak check."
  noisy=$(grep -E "^[0-9]{4}-" "$WATCH_LOG" | tail -30 | grep -vc " QUIET ")
  lines=$(grep -cE "^[0-9]{4}-" "$WATCH_LOG")
  [ "$lines" -ge 30 ] || die "the soak has only $lines watcher lines. Wait for 30 or more."
  [ "$noisy" = 0 ] || die "$noisy of the last 30 watcher lines are not QUIET. The soak has not passed."
  echo "PASS the last 30 PS0 watcher lines are QUIET"
fi
if [ -f "$WATCH_LOG" ]; then
  cp "$WATCH_LOG" "$OUT/ps0-soak.log"
  echo "# proof-run started $(date -Is). Faults follow. Evidence: $OUT" >> "$WATCH_LOG"
fi

VISR_TOKEN=$(kubectl -n aiops get secret visr-auth -o jsonpath='{.data.operator-token}' | base64 -d)
[ -n "$VISR_TOKEN" ] || die "the visr-auth Secret has no operator token"
export VISR_TOKEN API OUT

python3 - <<'PY'
import json, os, time, urllib.error, urllib.request

API, OUT, TOKEN = os.environ["API"], os.environ["OUT"], os.environ["VISR_TOKEN"]
T0 = time.time()
summary = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "api": API, "steps": {}}


def soak_stats(path):
    """The PS0 watcher lines as they stood before the first fault of this run."""
    try:
        with open(path) as f:
            rows = [line.split() for line in f if line.startswith("20")]
    except OSError:
        return None
    if not rows:
        return None
    quiet = [len(r) > 1 and r[1] == "QUIET" for r in rows]
    streak = 0
    for q in reversed(quiet):
        if not q:
            break
        streak += 1
    return {"lines": len(rows), "quiet": sum(quiet), "first": rows[0][0], "last": rows[-1][0],
            "quiet_streak_lines": streak, "quiet_since": rows[-streak][0] if streak else None}


summary["soak"] = soak_stats(os.path.join(OUT, "ps0-soak.log"))


def log(msg):
    print("%s %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def call(method, path, body=None, auth=True, timeout=15):
    """Return (status, parsed body). Never raises on an HTTP error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("X-Remote-User", "proof-run")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("X-Auth-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    except Exception as e:  # network failure: report it as status 0
        return 0, {"error": str(e)}
    try:
        return code, json.loads(raw or b"{}")
    except ValueError:
        return code, {"raw": raw[:300].decode("utf-8", "replace")}


def save(name, obj):
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def wait_for(pred, timeout, every=2.0):
    """Poll pred() until it returns a truthy value. Return (value, seconds waited) or (None, timeout)."""
    start = time.time()
    while time.time() - start < timeout:
        v = pred()
        if v:
            return v, round(time.time() - start, 1)
        time.sleep(every)
    return None, timeout


def graph():
    return call("GET", "/api/graph", auth=False)[1]


def root_of(g):
    r = g.get("root") or []
    return r[0] if r else None


def plant():
    return call("GET", "/api/plant", auth=False)[1]


def snap(t0, g=None):
    """One compact timeline row: what the console showed, and which machines had tripped."""
    g = g if g is not None else graph()
    devs = plant().get("devices") or {}
    r = root_of(g) or {}
    return {"t": round(time.time() - t0, 1), "root": r.get("pod"), "score": r.get("score"),
            "findings": sorted({f.get("pod") for f in g.get("findings") or []}),
            "incipient": [[i.get("pod"), i.get("eta_s")] for i in g.get("incipient") or []],
            "integrity": [x.get("kind") for x in g.get("integrity") or []],
            "loop_hops": sorted({e.get("dst") for e in g.get("edges") or []
                                 if e.get("src") == "chiller-1" and "loop" in (e.get("evidence") or [])}),
            "tripped": sorted(n for n, d in devs.items() if d.get("tripped"))}


def wait_calm(label, timeout=480):
    """Wait until the verdict has no root, no forecast card, and no open integrity finding."""
    def calm():
        g = graph()
        return not root_of(g) and not (g.get("incipient") or []) and not (g.get("integrity") or [])
    _, s = wait_for(calm, timeout, every=5.0)
    log("%s: calm after %s s" % (label, s))
    return s


# ---- 1. refusals ------------------------------------------------------------------------------
code, body = call("POST", "/api/scenarios/PS1/trigger", auth=False)
save("refusal_401", {"code": code, "body": body})
code409, body409 = call("POST", "/api/actions/execute", {"id": "0000000000000000"})
save("refusal_409", {"code": code409, "body": body409})
summary["steps"]["refusals"] = {"no_token": code, "stale_id": code409, "pass": code == 401 and code409 == 409}
log("refusals: no token -> %s, stale execute id -> %s" % (code, code409))

# ---- 2. PS1 in a compressor OFF window ----------------------------------------------------------
def compressor_amps():
    d = (plant().get("devices") or {}).get("compressor-1") or {}
    return d.get("amps")


log("waiting for the compressor to switch OFF (at most 320 s)")
on_seen = [False]


def off_edge():
    a = compressor_amps()
    if a is None:
        return None
    if a > 40:
        on_seen[0] = True
        return None
    return on_seen[0] and a < 20


_, waited = wait_for(off_edge, 320, every=1.0)
summary["steps"]["compressor_wait_s"] = waited
save("ps1_graph_before", graph())
code, body = call("POST", "/api/scenarios/PS1/trigger")
fired = time.time()
log("PS1 fired: %s %s" % (code, body))


ps1_timeline = []


def ps1_root():
    g = graph()
    ps1_timeline.append(snap(fired, g))
    r = root_of(g)
    if r and r.get("pod") == "press-1":
        return g
    return None


g1, ttv = wait_for(ps1_root, 300)
save("ps1_timeline", ps1_timeline)
step = {"fire_code": code, "time_to_verdict_s": ttv if g1 else None, "pass": bool(g1)}
trip_rows = [row for row in ps1_timeline if "press-1" in row["tripped"]]
step["press1_trip_s"] = trip_rows[0]["t"] if trip_rows else None
card_rows = [row for row in ps1_timeline if any(i[0] == "press-1" for i in row["incipient"])]
step["press1_first_card_s"] = card_rows[0]["t"] if card_rows else None
if g1:
    save("ps1_graph_verdict", g1)
    r = root_of(g1)
    edges = [e for e in g1.get("edges") or [] if e.get("src") == "press-1"]
    best = max(edges, key=lambda e: abs(e.get("r") or 0), default={})
    step.update(root=r.get("pod"), score=r.get("score"), confidence=best.get("confidence"),
                evidence=best.get("evidence"), signals=sorted({e.get("signal") for e in edges}),
                victims=[b.get("pod") for b in g1.get("blast_radius") or []])
    save("ps1_narrative", call("GET", "/api/narrative", auth=False, timeout=40)[1])
    log("PS1 verdict: root press-1 after %s s, evidence %s" % (ttv, best.get("evidence")))
else:
    log("PS1 verdict did not name press-1 within 300 s")
summary["steps"]["ps1"] = step

# ---- 3. Execute, relief, restore ----------------------------------------------------------------
def proposal():
    s, a = call("GET", "/api/actions", auth=False)
    for p in a.get("proposals") or []:
        if p.get("asset") == "press-1":
            return p
    return None


ex = {"pass": False}
prop, waited = wait_for(proposal, 90) if g1 else (None, 0)
if prop:
    save("execute_proposal", prop)
    # A relief number means nothing if the press had already tripped: record that state.
    ex["press1_tripped_at_execute"] = bool(((plant().get("devices") or {}).get("press-1") or {}).get("tripped"))
    code, body = call("POST", "/api/actions/execute", {"id": prop["id"]})
    t_exec = time.time()
    save("execute_response", {"code": code, "body": body})
    ex.update(code=code, target_pct=prop.get("to"))
    log("execute: %s" % code)

    def relief_row():
        rows = call("GET", "/api/audit?limit=50", auth=False)[1].get("entries") or []
        for e in reversed(rows):
            if e.get("verb") == "relief" and e.get("ts", 0) >= t_exec - 2:
                return e
        return None

    row, waited = wait_for(relief_row, 180, every=5.0)
    if row:
        save("execute_relief_row", row)
        ev = row.get("evidence") or {}
        ex.update(relief=ev)
        ex["pass"] = code == 200 and not ex["press1_tripped_at_execute"]
        log("relief: amps %s -> %s, rail volts %s -> %s" % (ev.get("amps_before"), ev.get("amps_after"),
                                                             ev.get("volts_before"), ev.get("volts_after")))
    code, body = call("POST", "/api/actions/restore", {"asset": "press-1"})
    save("restore_response", {"code": code, "body": body})
    ex["restore_code"] = code
else:
    log("no derate proposal for press-1 appeared")
summary["steps"]["execute"] = ex

# ---- 4. reset PS1 and time the clear -------------------------------------------------------------
code, _ = call("POST", "/api/scenarios/PS1/reset")
_, clear_s = wait_for(lambda: not root_of(graph()) and True, 420, every=5.0)
summary["steps"]["ps1_reset"] = {"code": code, "verdict_clear_s": clear_s}
log("PS1 reset: verdict clear after %s s" % clear_s)
time.sleep(60)

# ---- 5. PS5 forecast lead time -------------------------------------------------------------------
# PS1 leaves heat and learned edges behind. PS5 starts only from a calm verdict, like the 5b faults.
summary["steps"]["ps5_calm_before_s"] = wait_calm("before PS5")
code, body = call("POST", "/api/scenarios/PS5/trigger")
t5 = time.time()
log("PS5 fired: %s" % code)
cards, trips, timeline = {}, {}, []     # per machine: first card (time, eta) and first trip time
first_trip_t = None
deadline = t5 + 600
while time.time() < deadline:
    g = graph()
    row = snap(t5, g)
    timeline.append(row)
    for pod, eta in row["incipient"]:
        if pod not in cards:
            cards[pod] = {"at_s": row["t"], "eta_s": eta}
            log("PS5 trip card after %s s: %s trips in ~%s s" % (row["t"], pod, eta))
            if len(cards) == 1:
                save("ps5_graph_first_card", g)
    for pod in row["tripped"]:
        if pod not in trips:
            trips[pod] = row["t"]
            log("PS5 trip after %s s: %s" % (row["t"], pod))
            if first_trip_t is None:
                first_trip_t = row["t"]
                save("ps5_plant_trip", plant().get("devices") or {})
    # keep watching 60 s past the first trip, so the later cards and trips are on record too
    if first_trip_t is not None and row["t"] > first_trip_t + 60:
        break
    time.sleep(2)
save("ps5_timeline", timeline)
# Lead time per tripped machine: its own first card against its own trip. A card on another
# machine does not count.
per_machine = {pod: {"trip_s": t, "card_s": (cards.get(pod) or {}).get("at_s"),
                     "lead_s": round(t - cards[pod]["at_s"], 1) if pod in cards else None}
               for pod, t in sorted(trips.items(), key=lambda kv: kv[1])}
false_cards = sorted(set(cards) - set(trips))
first = next(iter(per_machine.items()), (None, {}))
ps5 = {"fire_code": code, "cards": cards, "trips": trips, "per_machine": per_machine,
       "first_trip": {"machine": first[0], **first[1]} if first[0] else {}, "cards_without_trip": false_cards}
ps5["lead_s"] = first[1].get("lead_s") if first[0] else None
ps5["pass"] = bool(first[0] and (first[1].get("lead_s") or 0) > 0)
summary["steps"]["ps5"] = ps5
code, _ = call("POST", "/api/scenarios/PS5/reset")
summary["steps"]["ps5_reset_code"] = code


# ---- 5b. the SCENARIOS.md faults: fire, time the evidence, reset, time the clear ----------------
def settle(label, timeout=480):
    """Wait until the verdict has no root, no forecast card, and no open integrity finding."""
    def calm():
        g = graph()
        return not root_of(g) and not (g.get("incipient") or []) and not (g.get("integrity") or [])
    _, s = wait_for(calm, timeout, every=5.0)
    log("%s: calm again after %s s" % (label, s))
    return s


def fault_step(sid, detect, timeout, note):
    """Fire sid, wait until detect(graph) returns evidence, save it, reset, and time the clear."""
    code, body = call("POST", "/api/scenarios/%s/trigger" % sid)
    t = time.time()
    log("%s fired: %s" % (sid, code))
    timeline = []

    def probe():
        g = graph()
        timeline.append(snap(t, g))
        return detect(g)

    hit, secs = wait_for(probe, timeout, every=3.0)
    save("%s_timeline" % sid.lower(), timeline)
    step = {"fire_code": code, "detected_s": secs if hit else None, "pass": bool(hit), "what": note}
    if hit:
        step["evidence"] = hit
        save("%s_graph_detected" % sid.lower(), graph())
        save("%s_plant_detected" % sid.lower(), plant())
        log("%s: %s after %s s" % (sid, note, secs))
    else:
        save("%s_graph_timeout" % sid.lower(), graph())
        log("%s: no %s within %s s" % (sid, note, timeout))
    code, _ = call("POST", "/api/scenarios/%s/reset" % sid)
    step["reset_code"] = code
    step["clear_s"] = settle(sid)
    step["total_s"] = round(time.time() - t, 1)
    summary["steps"][sid.lower()] = step
    time.sleep(60)
    return step


def root_is(pod):
    def f(g):
        r = root_of(g)
        if r and r.get("pod") == pod:
            best = max((e for e in g.get("edges") or [] if e.get("src") == pod),
                       key=lambda e: abs(e.get("r") or 0), default={})
            return {"root": pod, "score": r.get("score"), "evidence": best.get("evidence")}
        return None
    return f


def ps2_chain(g):
    base = root_is("compressor-1")(g)
    loop = [e for e in g.get("edges") or [] if e.get("src") == "chiller-1" and "loop" in (e.get("evidence") or [])]
    if base and loop:
        base["loop_hop"] = sorted({e["dst"] for e in loop})
        return base
    return None


def integrity_kind(kind):
    def f(g):
        for x in g.get("integrity") or []:
            if x.get("kind") == kind:
                return {k: x.get(k) for k in ("kind", "tag", "from", "to", "clients", "rail", "feeder_amps",
                                               "reported_amps", "gap_amps", "channel")}
        return None
    return f


def leak_card(g):
    for x in g.get("incipient") or []:
        if x.get("class") == "leak" and str(x.get("pod", "")).startswith("tag-server"):
            return {"pod": x.get("pod"), "eta_s": x.get("eta_s"), "value_mib": round((x.get("value") or 0) / 1048576, 1)}
    return None


settle("before the new faults")
fault_step("PS2", ps2_chain, 420, "root compressor-1 with the loop hop through chiller-1")
fault_step("PS3", root_is("hmi-gw"), 300, "root hmi-gw along segment field-1")
fault_step("PS4A", integrity_kind("unsigned_write"), 150, "an unsigned_write finding")
fault_step("PS4B", integrity_kind("current_balance"), 150, "a current_balance finding")

# PS6 ends with a real OOM kill of the tag server, so time each stage on its own
code, _ = call("POST", "/api/scenarios/PS6/trigger")
t6 = time.time()
log("PS6 fired: %s" % code)
card6, card_s = wait_for(lambda: leak_card(graph()), 360, every=3.0)
blind_s = None
blind, secs = wait_for(lambda: call("GET", "/api/tags", auth=False)[1].get("source") == "unavailable", 420, every=2.0)
if blind:
    blind_s = round(time.time() - t6, 1)
    save("ps6_tags_blind", call("GET", "/api/tags", auth=False)[1])
back, back_s = wait_for(lambda: call("GET", "/api/tags", auth=False)[1].get("source") == "scada", 240, every=3.0)
code, _ = call("POST", "/api/scenarios/PS6/reset")
summary["steps"]["ps6"] = {"first_card_s": card_s if card6 else None, "card": card6, "blind_after_s": blind_s,
                           "scada_back_after_blind_s": back_s if back else None, "reset_code": code,
                           "pass": bool(card6 and blind_s)}
log("PS6: card after %s s, blind after %s s, SCADA back %s s later" % (card_s, blind_s, back_s))

# ---- 6. ledger ----------------------------------------------------------------------------------
audit = call("GET", "/api/audit?limit=200", auth=False)[1]
save("audit_final", audit)
mine = [e for e in audit.get("entries") or [] if e.get("ts", 0) >= T0 - 1]
summary["steps"]["ledger"] = {"chain_ok": audit.get("chain_ok"), "count": audit.get("count"),
                              "rows_this_run": [[e.get("verb"), e.get("target"), e.get("status"), e.get("actor")]
                                                for e in mine]}
summary["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
summary["duration_s"] = round(time.time() - T0, 1)
save("summary", summary)

s = summary["steps"]
soak = summary.get("soak") or {}
lines = ["# VISR proof run", "", "Started %s, %s s." % (summary["started"], summary["duration_s"]), "",
         "| Check | Result |", "|---|---|",
         "| PS0 soak before the run | %s QUIET of %s lines, QUIET since %s |" % (
             soak.get("quiet"), soak.get("lines"), soak.get("quiet_since")),
         "| Fire without a token | %s |" % s["refusals"]["no_token"],
         "| Execute with a stale id | %s |" % s["refusals"]["stale_id"],
         "| PS1 time to verdict (root press-1) | %s s |" % s["ps1"].get("time_to_verdict_s"),
         "| PS1 evidence | %s on %s |" % (s["ps1"].get("evidence"), s["ps1"].get("signals")),
         "| PS1 first press-1 trip card, press-1 trip | %s s, %s s |" % (s["ps1"].get("press1_first_card_s"),
                                                                     s["ps1"].get("press1_trip_s")),
         "| Execute sent to a tripped press-1 | %s |" % s["execute"].get("press1_tripped_at_execute"),
         "| Execute relief, press-1 amps | %s -> %s |" % ((s["execute"].get("relief") or {}).get("amps_before"),
                                                        (s["execute"].get("relief") or {}).get("amps_after")),
         "| Execute relief, rail volts | %s -> %s |" % ((s["execute"].get("relief") or {}).get("volts_before"),
                                                       (s["execute"].get("relief") or {}).get("volts_after")),
         "| PS1 verdict clear after reset | %s s |" % s["ps1_reset"].get("verdict_clear_s"),
         "| PS5 first trip | %s at %s s |" % (s["ps5"].get("first_trip", {}).get("machine"),
                                            s["ps5"].get("first_trip", {}).get("trip_s")),
         "| PS5 card for that machine, lead time | %s s, %s s |" % (s["ps5"].get("first_trip", {}).get("card_s"),
                                                                  s["ps5"].get("lead_s")),
         "| PS5 cards on machines that did not trip | %s |" % s["ps5"].get("cards_without_trip"),
         "| PS2 root compressor-1 with the loop hop | %s s |" % s.get("ps2", {}).get("detected_s"),
         "| PS3 root hmi-gw | %s s |" % s.get("ps3", {}).get("detected_s"),
         "| PS4A unsigned write found | %s s (%s) |" % (s.get("ps4a", {}).get("detected_s"), (s.get("ps4a", {}).get("evidence") or {}).get("clients")),
         "| PS4B current balance found | %s s (gap %s A) |" % (s.get("ps4b", {}).get("detected_s"), (s.get("ps4b", {}).get("evidence") or {}).get("gap_amps")),
         "| PS6 leak card, then blind | %s s, %s s |" % (s.get("ps6", {}).get("first_card_s"), s.get("ps6", {}).get("blind_after_s")),
         "| Audit chain intact | %s (%s rows) |" % (s["ledger"]["chain_ok"], s["ledger"]["count"])]
with open(os.path.join(OUT, "summary.md"), "w") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
PY
rc=$?
unset VISR_TOKEN
[ -f "$WATCH_LOG" ] && echo "# proof-run ended $(date -Is), exit code $rc" >> "$WATCH_LOG"
echo
echo "== DONE $(date +%H:%M:%S). Evidence in $OUT"
exit $rc
