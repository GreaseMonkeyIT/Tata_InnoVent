#!/usr/bin/env bash
# SiliconKnights: soak / stress-test recorder for the VISR plant.
#
# Cycles the PS scenario set (SCENARIOS.md) for a set duration. The default set is PS1 PS2 PS3 PS4A
# PS4B PS5. It samples the live verdict every few seconds, then builds a self-contained HTML report
# that opens by double-click.
#
# Nothing is faked: each fault fires through the API console route (POST /api/scenarios/<id>/trigger).
# The API sends it to the fault owner: plant-sim perturbs the physics model, rogue-ews writes one real
# S7comm setpoint, and the tag server leaks real memory. The recorder only reads /api/graph,
# /api/narrative, /api/scenarios, /api/plant and /api/tags, and writes down what VISR decided.
# A mis-root shows as-is.
#
# PS6 stops a real service (the tag server runs out of memory). Run it only on its own: SCENARIOS=PS6.
#
# Run on the BOX (where kubectl talks to the cluster). Requires: bash, kubectl, curl, python3.
#
#   bash soak/soak.sh                 # 3h default, scenarios PS1 PS2 PS3 PS4A PS4B PS5
#   DURATION_H=1 bash soak/soak.sh    # shorter
#   SCENARIOS=PS6 DURATION_H=1 bash soak/soak.sh        # the edge scenario, alone
#   OBSERVE_PS3=240 COOLDOWN_PS3=200 bash soak/soak.sh  # per-id windows beat OBSERVE_S and COOLDOWN_S
#   API_BASE=http://localhost:8088 bash soak/soak.sh    # use curl instead of the kubectl proxy
#   API_BASE=http://localhost:8088 VISR_OPERATOR_TOKEN=<token> bash soak/soak.sh   # 2E auth enforced
#
# Stop early with Ctrl-C. The report is still built from whatever was captured.
set -uo pipefail

# ---- config (all env-overridable) --------------------------------------------------------------
DURATION_H=${DURATION_H:-3}                 # total run length (hours)
SCENARIOS=${SCENARIOS:-"PS1 PS2 PS3 PS4A PS4B PS5"}   # cycle order. PS6 runs alone.
SAMPLE_S=${SAMPLE_S:-12}                     # seconds between verdict samples
BASELINE_S=${BASELINE_S:-60}                 # quiet watch BEFORE each fire (confirm steady)
OBSERVE_S=${OBSERVE_S:-180}                  # watch window WHILE a scenario is firing
COOLDOWN_S=${COOLDOWN_S:-150}                # quiet watch AFTER reset (catch the self-clear)
# Per-id windows: OBSERVE_<ID> and COOLDOWN_<ID> override the two above for that id.
# PS2 needs time for the chiller relay to trip and the loop to heat (SCENARIOS.md 2.2).
# PS6 needs time for the leak to reach the limit, the kill, and the restart (SCENARIOS.md 2.7).
OBSERVE_PS2=${OBSERVE_PS2:-300}
OBSERVE_PS6=${OBSERVE_PS6:-420}
COOLDOWN_PS6=${COOLDOWN_PS6:-240}
NARR_EVERY=${NARR_EVERY:-5}                  # capture /api/narrative every Nth sample (LLM-backed → sparse)
AIOPS_NS=${AIOPS_NS:-aiops}
API_SVC=${API_SVC:-api}
API_PORT=${API_PORT:-8088}
API_BASE=${API_BASE:-}                       # set (e.g. http://localhost:8088) → use curl; else kubectl proxy
OPERATOR_TOKEN=${VISR_OPERATOR_TOKEN:-}      # 2E operator token; only the curl path can send it

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_ROOT=${OUT_ROOT:-"$SCRIPT_DIR/runs"}     # on the SSD (the repo working copy). Override to relocate.
RUN_ID="soak-$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$OUT_ROOT/$RUN_ID"
SAMPLES="$RUN_DIR/samples.jsonl"
TIMELINE="$RUN_DIR/timeline.csv"
LOG="$RUN_DIR/soak.log"
TICK="$RUN_DIR/.tick"                        # one JSON body per endpoint, rewritten every sample
mkdir -p "$RUN_DIR" "$TICK"

# The ids are upper case (SCENARIOS.md 1). The API also accepts lower case, but the phase names,
# the per-id env names and the report use upper case.
SCENARIOS=$(echo "$SCENARIOS" | tr '[:lower:]' '[:upper:]')

log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

# fetch a JSON endpoint → stdout (empty on any failure; never aborts the loop)
api_get(){
  if [ -n "$API_BASE" ]; then
    curl -fsS --max-time 12 "$API_BASE$1" 2>/dev/null || true
  else
    kubectl get --raw "/api/v1/namespaces/$AIOPS_NS/services/$API_SVC:$API_PORT/proxy$1" 2>/dev/null || true
  fi
}

# POST an API route (no body) → stdout. Returns nonzero on failure so the caller can log it.
api_post(){
  if [ -n "$API_BASE" ]; then
    local hdr=(-H "X-Remote-User: soak")
    [ -n "$OPERATOR_TOKEN" ] && hdr+=(-H "X-Auth-Token: $OPERATOR_TOKEN")
    curl -fsS --max-time 12 -X POST "${hdr[@]}" "$API_BASE$1" 2>>"$LOG"
  else
    kubectl create --raw "/api/v1/namespaces/$AIOPS_NS/services/$API_SVC:$API_PORT/proxy$1" -f /dev/null 2>>"$LOG"
  fi
}

FIRE_EPOCH=""; FIRE_OK=""
fire(){   # $1 = scenario id → the same console route the dashboard Fire button uses
  FIRE_EPOCH=$(date +%s); FIRE_OK=1
  api_post "/api/scenarios/$1/trigger" >>"$LOG" || { FIRE_OK=0; log "WARN: $1 trigger failed (see log)"; }
}

clear_fault(){   # the API resets the owner of this id; a short call, so the cadence holds
  api_post "/api/scenarios/$1/reset" >>"$LOG" || log "WARN: $1 reset failed (see log)"
}

# window <OBSERVE|COOLDOWN> <id> → seconds. OBSERVE_<ID> or COOLDOWN_<ID> wins over the global value.
window(){
  local kind=$1 id=$2 v def
  if [ "$kind" = OBSERVE ]; then def=$OBSERVE_S; else def=$COOLDOWN_S; fi
  if [[ $id =~ ^[A-Z0-9_]+$ ]]; then v="${kind}_$id"; echo "${!v:-$def}"; else echo "$def"; fi
}

# sample for $1 seconds, tagging each row phase=$2 cycle=$3 scenario=$4
sample_window(){
  local secs=$1 phase=$2 cyc=$3 scen=$4 i=0 end
  end=$(( $(date +%s) + secs ))
  while [ "$(date +%s)" -lt "$end" ]; do
    api_get /api/graph > "$TICK/graph.json"
    if [ $(( i % NARR_EVERY )) -eq 0 ]; then api_get /api/narrative > "$TICK/narr.json"; else : > "$TICK/narr.json"; fi
    api_get /api/scenarios > "$TICK/scenarios.json"
    api_get /api/plant > "$TICK/plant.json"
    api_get /api/tags > "$TICK/tags.json"
    GRAPH_FILE="$TICK/graph.json" NARR_FILE="$TICK/narr.json" SCEN_FILE="$TICK/scenarios.json" \
      PLANT_FILE="$TICK/plant.json" TAGS_FILE="$TICK/tags.json" \
      PHASE="$phase" CYCLE="$cyc" SCENARIO="$scen" FIRE_EPOCH="$FIRE_EPOCH" FIRE_OK="$FIRE_OK" \
      python3 "$SCRIPT_DIR/record.py" append "$SAMPLES" "$TIMELINE" 2>>"$LOG" || true
    i=$(( i + 1 ))
    sleep "$SAMPLE_S"
  done
}

build_report(){
  if [ -s "$SAMPLES" ]; then
    python3 "$SCRIPT_DIR/record.py" report "$RUN_DIR" "$SCRIPT_DIR/report_template.html" >>"$LOG" 2>&1 \
      && log "REPORT: $RUN_DIR/report.html" || log "WARN: report build failed (see log)"
  else
    log "no samples captured — no report built"
  fi
}
trap 'echo; log "stopping (signal) — finalizing"; build_report; exit 0' INT TERM

# ---- preflight ---------------------------------------------------------------------------------
DURATION_S=$(( DURATION_H * 3600 ))
WINDOWS=""
for s in $SCENARIOS; do WINDOWS="$WINDOWS $s:$(window OBSERVE "$s"):$(window COOLDOWN "$s")"; done
SCENARIOS="$SCENARIOS" DURATION_H="$DURATION_H" SAMPLE_S="$SAMPLE_S" OBSERVE_S="$OBSERVE_S" \
  COOLDOWN_S="$COOLDOWN_S" BASELINE_S="$BASELINE_S" WINDOWS="$WINDOWS" \
  python3 "$SCRIPT_DIR/record.py" meta "$RUN_DIR" 2>>"$LOG" || true

log "soak $RUN_ID — duration ${DURATION_H}h, scenarios: $SCENARIOS, sample ${SAMPLE_S}s → $RUN_DIR"
log "windows (id:observe_s:cooldown_s):$WINDOWS"
if [ -z "$(api_get /api/health)" ]; then
  log "ERROR: API not reachable. Either run 'kubectl port-forward svc/api -n $AIOPS_NS 8088:8088'"
  log "       and re-run with API_BASE=http://localhost:8088, or check the kubectl proxy path."
  exit 1
fi
AUTH=$(api_get /api/audit | python3 -c 'import json,sys; print(json.load(sys.stdin).get("auth",""))' 2>/dev/null || true)
if [ "$AUTH" = "enforced" ] && { [ -z "$API_BASE" ] || [ -z "$OPERATOR_TOKEN" ]; }; then
  log "ERROR: API auth is enforced (2E). Fire/reset needs the operator token, and only curl can send it."
  log "       Re-run with API_BASE=http://localhost:8088 (port-forward) and VISR_OPERATOR_TOKEN set."
  exit 1
fi
# The catalogue check only warns. An id the API cannot fire is still sampled, and the report shows the gap.
api_get /api/scenarios | IDS="$SCENARIOS" python3 -c '
import json, os, sys
try:
    cat = {str(s.get("id", "")).upper(): s for s in json.load(sys.stdin)}
except Exception:
    print("WARN: /api/scenarios did not answer with a list. The ids are not checked.")
    sys.exit()
for sid in os.environ["IDS"].split():
    if sid not in cat:
        print("WARN: %s is not in the API catalogue. Its trigger will fail." % sid)
    elif not cat[sid].get("triggerable"):
        print("WARN: %s is not triggerable through the API." % sid)
' 2>/dev/null | while IFS= read -r line; do log "$line"; done
case " $SCENARIOS " in
  *" PS6 "*) [ "$(echo "$SCENARIOS" | wc -w)" -gt 1 ] && log "WARN: PS6 stops the tag server. Run it alone (SCENARIOS=PS6)." ;;
esac
log "API reachable (auth: ${AUTH:-unknown}). PS0 must be silent (engine warm, LOG-035 soak) before you trust cycle 1."

# ---- main loop ---------------------------------------------------------------------------------
START=$(date +%s); END=$(( START + DURATION_S )); cycle=0
while [ "$(date +%s)" -lt "$END" ]; do
  cycle=$(( cycle + 1 ))
  for s in $SCENARIOS; do
    [ "$(date +%s)" -lt "$END" ] || break
    log "cycle $cycle · baseline → $s"
    FIRE_EPOCH=""; FIRE_OK=""
    sample_window "$BASELINE_S" "baseline" "$cycle" "$s"
    log "cycle $cycle · FIRE $s"
    fire "$s"
    sample_window "$(window OBSERVE "$s")" "$s" "$cycle" "$s"
    log "cycle $cycle · reset $s"
    clear_fault "$s"
    sample_window "$(window COOLDOWN "$s")" "cooldown" "$cycle" "$s"
  done
done

log "soak complete after $cycle cycle(s)"
build_report
