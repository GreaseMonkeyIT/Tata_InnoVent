#!/usr/bin/env bash
# SiliconKnights — soak / stress-test recorder.
#
# Runs the REAL fault scenarios (S1, S2, S3, S5) back-to-back for a set duration, sampling the live
# causal verdict onto the SSD every few seconds, then builds a self-contained HTML report you open
# by double-click (think `powercfg /batteryreport`, but for the causal engine).
#
# Nothing is faked: faults fire through the canonical scenarios/<id>/trigger.sh scripts (real fio
# storms, real CPU bursts, the real OOM-killer). The recorder just watches /api/graph and writes
# down what the engine decided. S2/S3 will show their known limits as-is — that's the honest point.
#
# Run on the BOX (where kubectl talks to the cluster). Requires: bash, kubectl, python3.
#
#   bash soak/soak.sh                 # 3h default, scenarios S1 S2 S3 S5
#   DURATION_H=1 bash soak/soak.sh    # shorter
#   API_BASE=http://localhost:8088 bash soak/soak.sh    # use curl instead of the kubectl proxy
#
# Stop early with Ctrl-C — the report is still built from whatever was captured.
set -uo pipefail

# ---- config (all env-overridable) --------------------------------------------------------------
DURATION_H=${DURATION_H:-3}                 # total run length (hours)
SCENARIOS=${SCENARIOS:-"S1 S2 S3 S5"}       # cycle order
SAMPLE_S=${SAMPLE_S:-12}                     # seconds between verdict samples
BASELINE_S=${BASELINE_S:-60}                 # quiet watch BEFORE each fire (confirm steady)
OBSERVE_S=${OBSERVE_S:-180}                  # watch window WHILE a scenario is firing
COOLDOWN_S=${COOLDOWN_S:-150}                # quiet watch AFTER reset (catch the self-clear)
NARR_EVERY=${NARR_EVERY:-5}                  # capture /api/narrative every Nth sample (LLM-backed → sparse)
AIOPS_NS=${AIOPS_NS:-aiops}
API_SVC=${API_SVC:-api}
API_PORT=${API_PORT:-8088}
API_BASE=${API_BASE:-}                       # set (e.g. http://localhost:8088) → use curl; else kubectl proxy

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
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

fire(){   # $1 = scenario id → use the canonical trigger script
  if [ -x "$REPO_DIR/scenarios/$1/trigger.sh" ] || [ -f "$REPO_DIR/scenarios/$1/trigger.sh" ]; then
    bash "$REPO_DIR/scenarios/$1/trigger.sh" >>"$LOG" 2>&1 || log "WARN: $1 trigger returned nonzero"
  else
    log "WARN: no trigger.sh for $1 — skipping fire"
  fi
}

clear_fault(){   # mirror scenarios/<id>/reset.sh, but inline so it never blocks the sampling cadence
  case "$1" in
    S1) local p; p=$(kubectl get pod -n factory-data -l app=cooling-monitor -o name 2>/dev/null | head -1)
        [ -n "$p" ] && kubectl exec -n factory-data "$p" -- rm -f /shared/cooling/FLUSH >>"$LOG" 2>&1 || true ;;
    S2) kubectl delete job log-archiver-s2 -n factory-data --ignore-not-found >>"$LOG" 2>&1 || true ;;
    S3) kubectl get jobs -n factory-data -o name 2>/dev/null | grep '/s3-run-' \
          | xargs -r kubectl delete -n factory-data >>"$LOG" 2>&1 || true ;;
    S5) kubectl set env deploy/vision-qc -n factory-edge LEAK_ENABLED=false >>"$LOG" 2>&1 || true ;;
  esac
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
log "API reachable. (Tip: make sure S0 is silent — engine warmed — before relying on the first cycle.)"

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
