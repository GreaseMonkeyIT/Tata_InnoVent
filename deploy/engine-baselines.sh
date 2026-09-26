#!/usr/bin/env bash
# engine-baselines.sh: learn, then lock the engine baselines (LOG-089).
# Run it on the box as the normal user (no sudo):
#   bash ~/Tata_InnoVent/deploy/engine-baselines.sh status   # read-only: learning or locked, and the baselines
#   bash ~/Tata_InnoVent/deploy/engine-baselines.sh lock     # after the PS0 soak passes
#   bash ~/Tata_InnoVent/deploy/engine-baselines.sh unlock   # before a new soak
#   bash ~/Tata_InnoVent/deploy/engine-baselines.sh bands "2026-09-26 20:51"   # the display bands of a soak
#                                                              # that ended then (lock runs it for now)
#
# Why: the engine learns each signal's normal band while it runs. A long fault that leaves a flat
# level taught the rail B baselines the fault itself on 2026-09-25, and every normal compressor cycle
# then raised a compressor-1 root. Locked, a mature baseline never changes. A new asset still learns
# until its baseline is mature (a provisional baseline), then it stays too.
# How: the engine checks the file $LOCK on every pass and reports meta.baselines on /api/graph.
# The factory-up wipe removes the file, so a fresh memory always starts learning.
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
LOCK=/var/lib/skn/memory/baselines.lock
GRAPH=/api/v1/namespaces/aiops/services/api:8088/proxy/api/graph
die() { echo "STOP $*"; exit 1; }

state() {   # meta.baselines as the engine reports it: learning, locked, or unknown
  kubectl get --raw "$GRAPH" 2>/dev/null | python3 -c '
import json, sys
try:
    print((json.load(sys.stdin).get("meta") or {}).get("baselines") or "unknown")
except ValueError:
    print("unknown")'
}

wait_for() {   # wait_for <state>: the engine reads the file on its next pass (every 10 s)
  local s
  for _ in $(seq 1 12); do
    s=$(state)
    [ "$s" = "$1" ] && { echo "PASS the engine reports baselines: $s"; return 0; }
    sleep 5
  done
  die "the engine still reports baselines: $s (expected $1)"
}

# LOG-103: the display bands. The console grades a machine's throughput against the range that the
# machine showed during the soak, not against one fixed number. compressor-1 runs loaded and unloaded
# (25 % to 100 %) in normal duty, so a fixed "under 70 % is a fault" rule painted it red all day.
# bands [END] learns p01..p99 of plant_throughput_pct over the BANDS_H hours before END (default: now)
# and writes ConfigMap aiops/display-bands. `lock` runs it for the soak that just passed.
BANDS_H=${BANDS_H:-2}
bands() {
  local end prom
  end=$(date -d "${1:-now}" +%s) || die "bad time '${1:-}'"
  prom=http://$(kubectl -n observability get svc prom-kube-prometheus-stack-prometheus -o jsonpath='{.spec.clusterIP}'):9090
  PROM="$prom" END="$end" H="$BANDS_H" python3 - > /tmp/display-bands.json <<'PY' || die "the bands could not be learned"
import json, os, sys, time, urllib.parse, urllib.request
prom, end, h = os.environ["PROM"], int(os.environ["END"]), int(os.environ["H"])
def q(expr):
    url = prom + "/api/v1/query?" + urllib.parse.urlencode({"query": expr, "time": end})
    return {r["metric"].get("pod"): float(r["value"][1])
            for r in json.load(urllib.request.urlopen(url, timeout=15))["data"]["result"]}
w = "plant_throughput_pct[%dh]" % h
lo, hi, n = q("quantile_over_time(0.01, %s)" % w), q("quantile_over_time(0.99, %s)" % w), q("count_over_time(%s)" % w)
need = h * 3600 / 5 * 0.9                  # a band needs 90 % of the 5 s samples of the window
bands = {p: [round(lo[p], 1), round(hi[p], 1)] for p in lo if p in hi and n.get(p, 0) >= need}
if not bands:
    sys.exit("no machine has a full window of throughput samples")
print(json.dumps({"signal": "plant_throughput_pct", "quantiles": [0.01, 0.99], "hours": h,
                  "end": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(end)), "bands": bands}, indent=1))
PY
  kubectl -n aiops create configmap display-bands --from-file=bands.json=/tmp/display-bands.json \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null || die "could not write ConfigMap aiops/display-bands"
  echo "PASS display bands learned over the $BANDS_H h before $(date -d "@$end" +%F\ %T):"
  python3 -c 'import json; d=json.load(open("/tmp/display-bands.json")); [print("  %-14s %5.1f .. %5.1f %%" % (p, *b)) for p, b in sorted(d["bands"].items())]'
}

baselines() {   # every baseline row: workload, signal, n, and the incident threshold
  kubectl -n aiops exec -i deploy/correlation-engine -- python3 - <<'PY'
import glob, sqlite3
for f in sorted(glob.glob("/var/lib/skn/memory/*.db")):
    db = sqlite3.connect("file:%s?mode=ro" % f, uri=True)
    try:
        rows = db.execute("SELECT workload, signal, median, mad, n FROM baselines ORDER BY signal, workload").fetchall()
    except sqlite3.Error:
        continue
    for wl, sig, med, mad, n in rows:
        print("  %-14s %-22s n=%-6d median=%-9.3f mad=%.3f%s" % (sig, wl, n, med, mad, "" if n >= 12 else "  (still learning)"))
PY
}

case "${1:-status}" in
  status)
    echo "engine reports baselines: $(state)"
    if kubectl -n aiops exec deploy/correlation-engine -- test -e "$LOCK" 2>/dev/null; then
      echo "lock file: present"
    else
      echo "lock file: absent"
    fi
    baselines
    ;;
  lock)
    screen -ls 2>/dev/null | grep -q '\.visr-ps0[[:space:]]' \
      && echo "NOTE the PS0 soak watcher still runs. Lock only after its last 30 lines are QUIET."
    kubectl -n aiops exec deploy/correlation-engine -- touch "$LOCK" || die "could not write $LOCK"
    wait_for locked
    baselines
    bands now
    ;;
  bands)
    bands "${2:-now}"
    ;;
  unlock)
    kubectl -n aiops exec deploy/correlation-engine -- rm -f "$LOCK" || die "could not remove $LOCK"
    wait_for learning
    ;;
  *)
    die "usage: engine-baselines.sh status|lock|unlock|bands [END]"
    ;;
esac
