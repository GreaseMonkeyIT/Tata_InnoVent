#!/usr/bin/env bash
# refusals.sh: show the api refusing three commands, and the ledger rows that each refusal writes.
# Use it for the refusals beat on camera (SCENARIOS.md section 9). It changes no plant state.
#
#   bash ~/Tata_InnoVent/deploy/refusals.sh
#
# Checks:
#   1. A fire command with no operator token must get 401.
#   2. An execute with a proposal id that no current verdict supports must get 409.
#   3. A delete of the base PLC must get 403. The script sends it only after /api/fleet confirms that
#      plc-stamping carries the label visr/managed=static. Else it skips the check. It never deletes.
# Then it prints the ledger rows written since the start, and the hash chain check.
set -euo pipefail
export KUBECONFIG="$HOME/.kube/config"
API=${API:-http://127.0.0.1:30088}
BASE_PLC=${BASE_PLC:-plc-stamping}
FAILS=0
t0=$(date +%s)

TOKEN=$(kubectl -n aiops get secret visr-auth -o jsonpath='{.data.operator-token}' | base64 -d)
[ -n "$TOKEN" ] || { echo "STOP the visr-auth Secret has no operator token"; exit 1; }
curl -s -m 8 "$API/api/health" | grep -q '"auth":"enforced"' || { echo "STOP the api does not enforce auth"; exit 1; }

check() {   # check <label> <want> <got>
  if [ "$2" = "$3" ]; then echo "PASS $1: $3"; else echo "FAIL $1: want $2, got $3"; FAILS=$((FAILS + 1)); fi
}

got=$(curl -s -o /dev/null -m 10 -w '%{http_code}' -X POST -H "X-Remote-User: refusals" \
  "$API/api/scenarios/PS1/trigger")
check "fire with no operator token" 401 "$got"

got=$(curl -s -o /dev/null -m 15 -w '%{http_code}' -X POST -H "X-Auth-Token: $TOKEN" \
  -H "X-Remote-User: refusals" -H "Content-Type: application/json" \
  -d '{"id":"0000000000000000"}' "$API/api/actions/execute")
check "execute with a stale proposal id" 409 "$got"

managed=$(curl -s -m 15 "$API/api/fleet" | python3 -c '
import json, sys
name = sys.argv[1]
d = json.load(sys.stdin)
for p in d.get("plcs") or []:
    if p.get("name") == name:
        print(p.get("managed") or "")
        break
' "$BASE_PLC")
if [ "$managed" = "static" ]; then
  got=$(curl -s -o /dev/null -m 15 -w '%{http_code}' -X DELETE -H "X-Auth-Token: $TOKEN" \
    -H "X-Remote-User: refusals" "$API/api/fleet/plcs/$BASE_PLC")
  check "delete of the base PLC $BASE_PLC" 403 "$got"
else
  echo "SKIP delete of the base PLC: /api/fleet does not show $BASE_PLC as static (managed='$managed')"
fi
unset TOKEN

echo
echo "Ledger rows since the start:"
curl -s -m 10 "$API/api/audit?limit=50" | python3 -c '
import json, sys, time
t0 = float(sys.argv[1])
d = json.load(sys.stdin)
for e in d.get("entries") or []:
    if e.get("ts", 0) >= t0 - 1:
        print("  %s  %-12s %-18s %-26s %s" % (time.strftime("%H:%M:%S", time.localtime(e["ts"])),
              e.get("verb"), str(e.get("target"))[:18], e.get("status"), e.get("actor")))
print("chain intact: %s (%s rows)" % (d.get("chain_ok"), d.get("count")))
' "$t0"
echo
[ "$FAILS" = 0 ] && echo "== DONE all refusals as expected" || echo "== DONE $FAILS refusal checks failed"
exit "$FAILS"
