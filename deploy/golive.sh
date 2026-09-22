#!/usr/bin/env bash
# golive.sh: deploy the whole VISR stack onto a running k3s from the box registry. No sudo.
# Run it after deploy/resume.sh:
#   bash ~/Tata_InnoVent/deploy/golive.sh 2>&1 | tee /var/tmp/visr-golive.log
# Steps: preflight, old-bench cleanup, apply every manifest in the PIVOT_SETUP order, restart every
# Deployment onto the registry images, then verify from outside and inside the cluster.
# It does NOT wipe the engine memory. Do that right before the soak (PIVOT_SETUP.md step 2).
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1
FAIL=0
step() { echo; echo "== $(date +%H:%M:%S) $*"; }
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1${2:+  [$2]}"; FAIL=$((FAIL + 1)); }
code() { curl -sk -o /dev/null -m 8 -w '%{http_code}' "$@"; }

# in-cluster JSON probe: pyget <ns> <deploy> <url> <python expression over d>
pyget() {
  kubectl -n "$1" exec "deploy/$2" -- python -c "import json,urllib.request as u
d=json.load(u.urlopen('$3', timeout=5))
print('OK' if ($4) else 'NO')" 2>/dev/null | grep -qx OK
}
wait_for() {   # wait_for <label> <seconds> <command...>
  local label=$1 secs=$2; shift 2
  for _ in $(seq 1 $((secs / 5))); do
    if "$@"; then pass "$label"; return 0; fi
    sleep 5
  done
  fail "$label" "not true after ${secs}s"
}

step "0 preflight"
if ! kubectl get nodes 2>/dev/null | grep -q " Ready"; then
  echo "k3s is not Ready. Run deploy/resume.sh first."; exit 1
fi
for s in "aiops visr-auth" "aiops visr-tls" "aiops visr-fleet" "plant visr-fleet" "fleet plc-stamping-token"; do
  set -- $s
  kubectl -n "$1" get secret "$2" >/dev/null 2>&1 || { echo "Secret $1/$2 is missing. Run deploy/resume.sh."; exit 1; }
done
# LOG-076: the historian password moved out of the manifests into Secret plant/historian-auth.
# The script makes it once and changes the password in a running historian. Later runs change nothing.
bash deploy/historian-auth.sh || { echo "deploy/historian-auth.sh failed. The manifests were not applied."; exit 1; }
# LOG-077: the OpenPLC web password moved from the vendor default into Secret plant/openplc-auth.
# The script makes the Secret once. plc/entrypoint.sh sets the password at each OpenPLC pod start.
bash deploy/openplc-auth.sh || { echo "deploy/openplc-auth.sh failed. The manifests were not applied."; exit 1; }
curl -sf -m 5 http://127.0.0.1:5000/v2/_catalog >/dev/null || { echo "the registry on 127.0.0.1:5000 does not answer"; exit 1; }
chmod +x deploy/skctl deploy/*.sh soak/*.sh plc/*.sh 2>/dev/null || true
pass "node Ready, Secrets present, registry up"

step "1 remove old-bench leftovers (operator approved 2026-09-15)"
for rel in "observability beyla" "default factory"; do
  set -- $rel
  if helm -n "$1" status "$2" >/dev/null 2>&1; then helm -n "$1" uninstall "$2" && echo "removed helm release $1/$2"; fi
done
kubectl delete ns factory-core factory-data factory-edge chaos --ignore-not-found --wait=false

step "2 apply the manifests (PIVOT_SETUP section 5 order, no helm upgrades)"
bash deploy/skctl up --components engine,language,dashboard
kubectl apply -f deploy/grafana-psi-dashboard.yaml -f deploy/grafana-plant-dashboard.yaml
# LOG-062: Grafana serves from /grafana/, and the HTTPS console proxies that path on its own origin.
# kubectl set env, never a helm upgrade. The same values again change nothing and restart nothing.
kubectl -n observability set env deploy/prom-grafana -c grafana \
  GF_SERVER_ROOT_URL='%(protocol)s://%(domain)s:%(http_port)s/grafana/' GF_SERVER_SERVE_FROM_SUB_PATH=true
kubectl apply -f plant/deploy.yaml
kubectl apply -f deploy/openplc.yaml
kubectl apply -f scada/deploy.yaml
kubectl apply -f deploy/fleet.yaml
kubectl apply -f deploy/rogue-ews.yaml     # SCENARIOS.md 2.4: the PS4A fault injector (not a PLC)

step "3 restart every Deployment onto the registry images"
kubectl -n aiops rollout restart deploy/aggregator deploy/correlation-engine deploy/api deploy/dashboard
kubectl -n plant rollout restart deploy/plant-sim deploy/openplc deploy/tag-server deploy/rogue-ews
kubectl -n fleet rollout restart deploy/plc-stamping
for d in "aiops aggregator" "aiops correlation-engine" "aiops api" "aiops dashboard" \
         "plant plant-sim" "plant openplc" "plant tag-server" "plant rogue-ews" "fleet plc-stamping" \
         "observability prom-grafana"; do
  set -- $d
  if kubectl -n "$1" rollout status "deploy/$2" --timeout=300s >/dev/null; then pass "rollout $1/$2"
  else fail "rollout $1/$2"; fi
done

step "4 verify the front door (2E)"
# The NodePort can route to the old dashboard pod for a few seconds after the rollout (LOG-066).
wait_for "dashboard login wall answers 401" 60 sh -c '[ "$(curl -sk -o /dev/null -m 8 -w "%{http_code}" https://127.0.0.1:30443/)" = 401 ]'
wait_for "dashboard /healthz answers 200" 60 sh -c '[ "$(curl -sk -o /dev/null -m 8 -w "%{http_code}" https://127.0.0.1:30443/healthz)" = 200 ]'
wait_for "http redirects to https" 60 sh -c '[ "$(curl -sk -o /dev/null -m 8 -w "%{http_code}" http://127.0.0.1:30080/)" = 301 ]'
curl -s -m 8 http://127.0.0.1:30088/api/health | grep -q '"auth":"enforced"' && pass "api auth enforced" || fail "api auth enforced"
[ "$(code -X POST http://127.0.0.1:30088/api/scenarios/PS1/trigger)" = 401 ] && pass "anonymous fire gets 401" || fail "anonymous fire gets 401"
[ "$(code -X POST http://127.0.0.1:30088/api/scenarios/PS4A/trigger)" = 401 ] && pass "anonymous PS4A fire gets 401" || fail "anonymous PS4A fire gets 401"
wait_for "grafana serves the /grafana/ sub-path" 90 sh -c '[ "$(curl -s -o /dev/null -m 8 -w "%{http_code}" http://127.0.0.1:30030/grafana/api/health)" = 200 ]'
[ "$(code https://127.0.0.1:30443/grafana/api/health)" = 401 ] && pass "console /grafana/ route sits behind the login wall" || fail "console /grafana/ route"

step "5 verify the plant, the PLCs, and SCADA"
wait_for "plant-sim answers /state" 60 pyget plant plant-sim http://127.0.0.1:9200/state "len(d['devices']) >= 8"
wait_for "rail psu-c exists" 30 pyget plant plant-sim http://127.0.0.1:9200/state "'psu-c' in d['rails']"
wait_for "stamping cell closed-loop with plc-stamping" 120 pyget plant plant-sim http://127.0.0.1:9200/cells "d['cells']['stamping']['mode'] == 'closed-loop'"
wait_for "OpenPLC trip loop closed-loop" 240 pyget plant plant-sim http://127.0.0.1:9200/state "d['plc']['mode'] == 'closed-loop'"
# LOG-077: the OpenPLC web UI stays on NodePort 30081, it takes only the Secret password, and nothing
# answers on the REST API port 8443. No PS verdict reads the web UI, so these checks report and do not
# stop the run. An image from before LOG-077 gives INFO lines here until deploy/openplc-rollout.sh deploy.
[ "$(code http://127.0.0.1:30081/login)" = 200 ] && pass "OpenPLC web UI answers on NodePort 30081" \
  || echo "INFO the OpenPLC web UI does not answer on NodePort 30081"
read -r p_dflt p_secret p_prog p_rest <<< "$(bash deploy/openplc-rollout.sh probe)"
[ "${p_dflt:-}" = 200 ] && pass "OpenPLC refuses the vendor default login" \
  || echo "INFO OpenPLC answers ${p_dflt:-nothing} to the vendor default login, not 200 (refused). LOG-077 image not deployed?"
[ "${p_secret:-}" = 302 ] && pass "OpenPLC takes the password from Secret plant/openplc-auth" \
  || echo "INFO OpenPLC answers ${p_secret:-nothing} to the Secret password, not 302 (taken)"
[ "${p_prog:-}" = plant_trips ] && pass "the OpenPLC dashboard shows plant_trips in Running" \
  || echo "INFO the OpenPLC dashboard check gave ${p_prog:-nothing}, not plant_trips (it needs the Secret login)"
[ "${p_rest:-}" = 000 ] && pass "nothing answers on the OpenPLC REST API port 8443" \
  || echo "INFO the OpenPLC REST API port 8443 answers ${p_rest:-nothing}. LOG-077 image not deployed?"
wait_for "tag server reads OpenPLC" 120 pyget plant tag-server http://127.0.0.1:9300/tags "d['plc_connected']"
# LOG-076: the historian password comes from Secret plant/historian-auth. No PS verdict reads the
# historian, so this check reports and does not stop the run.
hist=0
for _ in $(seq 1 36); do
  if pyget plant tag-server http://127.0.0.1:9300/tags "d['historian']['connected']"; then hist=1; break; fi
  sleep 5
done
if [ "$hist" = 1 ]; then pass "tag server writes the historian"
else echo "INFO the tag server has no historian connection after 180 s (check Secret plant/historian-auth, LOG-076)"; fi
wait_for "plc-stamping enrolled and connected over S7comm" 120 pyget plant tag-server http://127.0.0.1:9300/fleet "any(p['name'] == 'plc-stamping' and p['connected'] for p in d)"
wait_for "fleet tags GOOD" 60 pyget plant tag-server http://127.0.0.1:9300/fleet "all(t['quality'] == 'GOOD' for p in d if p['name'] == 'plc-stamping' for t in p['tags'])"
# SCENARIOS.md 3 and 9: the new plant model parts and the fault owners answer
wait_for "plant-sim rails carry feeder amps" 30 pyget plant plant-sim http://127.0.0.1:9200/state "all('amps' in r for r in d['rails'].values())"
wait_for "plant-sim segment field-1 exists" 30 pyget plant plant-sim http://127.0.0.1:9200/state "'field-1' in (d.get('segments') or {})"
wait_for "rogue-ews answers /state" 60 pyget plant rogue-ews http://127.0.0.1:8090/state "'active' in d"
wait_for "tag server answers /chaos" 30 pyget plant tag-server http://127.0.0.1:9300/chaos "'active' in d"

step "6 verify the engine and the api views"
wait_for "engine graph answers without error" 120 pyget aiops api http://127.0.0.1:8088/api/graph "d['meta'].get('status') != 'error'"
wait_for "api /api/fleet lists plc-stamping in RUN" 90 pyget aiops api http://127.0.0.1:8088/api/fleet "any(p['name'] == 'plc-stamping' and p['state'] == 'RUN' for p in d['plcs'])"
wait_for "api /api/tags from SCADA" 60 pyget aiops api http://127.0.0.1:8088/api/tags "d.get('source') == 'scada'"
wait_for "api catalogue lists PS0 to PS6" 30 pyget aiops api http://127.0.0.1:8088/api/scenarios "{'PS0','PS1','PS2','PS3','PS4A','PS4B','PS5','PS6'} <= {s['id'] for s in d}"
wait_for "api integrity checks run live" 90 pyget aiops api http://127.0.0.1:8088/api/integrity "d.get('source') == 'live'"
pyget aiops api http://127.0.0.1:8088/api/topology "d.get('source') == 'caretta'" && pass "caretta topology" || echo "INFO caretta topology unavailable (EDGE view beat can be trimmed)"

step "7 state"
kubectl get pods -A -o wide | grep -Ev "kube-system" | head -40
echo
echo "== DONE failures=$FAIL  dashboard: https://100.93.123.48:30443 (Tailscale) or https://192.168.1.239:30443 (LAN)"
exit "$FAIL"
