#!/usr/bin/env bash
# SiliconKnights: soak / stress-test recorder for the VISR plant.
#
# Cycles the PS-series plant faults (PS1, PS2, PS5) for a set duration. It samples the live causal
# verdict every few seconds, then builds a self-contained HTML report that opens by double-click.
#
# Nothing is faked: each fault fires through the API console route (POST /api/scenarios/<id>/trigger),
# which perturbs the physics model in the plant sim. The recorder only watches /api/graph and writes
# down what the engine decided, so a mis-root shows as-is.
#
# Run on the BOX (where kubectl talks to the cluster). Requires: bash, kubectl, curl, python3.
#
#   bash soak/soak.sh                 # 3h default, scenarios PS1 PS2 PS5
#   DURATION_H=1 bash soak/soak.sh    # shorter
#   API_BASE=http://localhost:8088 bash soak/soak.sh    # use curl instead of the kubectl proxy
#   API_BASE=http://localhost:8088 VISR_OPERATOR_TOKEN=<token> bash soak/soak.sh   # 2E auth enforced
#
# Stop early with Ctrl-C. The report is still built from whatever was captured.
set -uo pipefail

# ---- config (all env-overridable) --------------------------------------------------------------
DURATION_H=${DURATION_H:-3}                 # total run length (hours)
SCENARIOS=${SCENARIOS:-"PS1 PS2 PS5"}       # cycle order
SAMPLE_S=${SAMPLE_S:-12}                     # seconds between verdict samples
BASELINE_S=${BASELINE_S:-60}                 # quiet watch BEFORE each fire (confirm steady)
OBSERVE_S=${OBSERVE_S:-180}                  # watch window WHILE a scenario is firing
COOLDOWN_S=${COOLDOWN_S:-150}                # quiet watch AFTER reset (catch the self-clear)
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
mkdir -p "$RUN_DIR"

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

fire(){   # $1 = scenario id → the same console route the dashboard Fire button uses
  api_post "/api/scenarios/$1/trigger" >>"$LOG" || log "WARN: $1 trigger failed (see log)"
}

clear_fault(){   # the sim's /reset clears every active plant fault; a short call, so the cadence holds
  api_post "/api/scenarios/$1/reset" >>"$LOG" || log "WARN: $1 reset failed (see log)"
}

# sample for $1 seconds, tagging each row phase=$2 cycle=$3
sample_window(){
  local secs=$1 phase=$2 cyc=$3 i=0 end
  end=$(( $(date +%s) + secs ))
  while [ "$(date +%s)" -lt "$end" ]; do
    local g n=""
    g=$(api_get /api/graph)
    if [ $(( i % NARR_EVERY )) -eq 0 ]; then n=$(api_get /api/narrative); fi
    GRAPH_JSON="$g" NARR_JSON="$n" PHASE="$phase" CYCLE="$cyc" \
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
SCENARIOS="$SCENARIOS" DURATION_H="$DURATION_H" SAMPLE_S="$SAMPLE_S" OBSERVE_S="$OBSERVE_S" \
  COOLDOWN_S="$COOLDOWN_S" python3 "$SCRIPT_DIR/record.py" meta "$RUN_DIR" 2>>"$LOG" || true

log "soak $RUN_ID — duration ${DURATION_H}h, scenarios: $SCENARIOS, sample ${SAMPLE_S}s → $RUN_DIR"
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
log "API reachable (auth: ${AUTH:-unknown}). PS0 must be silent (engine warm, LOG-035 soak) before you trust cycle 1."

# ---- main loop ---------------------------------------------------------------------------------
START=$(date +%s); END=$(( START + DURATION_S )); cycle=0
while [ "$(date +%s)" -lt "$END" ]; do
  cycle=$(( cycle + 1 ))
  for s in $SCENARIOS; do
    [ "$(date +%s)" -lt "$END" ] || break
    log "cycle $cycle · baseline → $s"
    sample_window "$BASELINE_S" "baseline" "$cycle"
    log "cycle $cycle · FIRE $s"
    fire "$s"
    sample_window "$OBSERVE_S" "$s" "$cycle"
    log "cycle $cycle · reset $s"
    clear_fault "$s"
    sample_window "$COOLDOWN_S" "cooldown" "$cycle"
  done
done

log "soak complete after $cycle cycle(s)"
build_report
