#!/usr/bin/env bash
# faults.sh: the fault shell. It fires and resets the plant scenarios from a terminal on the box,
# so the operator console has no fault controls (LOG-081). Reach the box over Tailscale SSH:
#
#   ssh -t forge 'bash ~/Tata_InnoVent/deploy/faults.sh'        # open the standby shell
#   ssh forge 'bash ~/Tata_InnoVent/deploy/faults.sh fire 1'     # run one command, then exit
#
# Commands, typed in the shell or given as arguments:
#   status          (s)   every scenario and its state
#   fire <n>        (f)   fire scenario n: 1, 2, 3, 4a, 4b, 5, 6
#   reset <n>       (r)   reset scenario n
#   reset all       (r all) reset every fault owner (plant-sim, rogue-ews, tag server)
#   help (h), quit (q)
#
# The standby shell runs in the screen session visr-faults. Ctrl-A D detaches it, and it stays on
# standby. The next call attaches to the same session.
# The operator token comes from the Secret aiops/visr-auth. It stays in this process and is never
# printed. The api writes every fire and reset to the ledger with the actor "fault-shell".
# While the PS0 soak watcher (screen visr-ps0) runs, a fire asks for a confirmation, because a fault
# ends the soak. Without a terminal, the fire stops unless FORCE=1.
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
API=${API:-http://127.0.0.1:30088}
ACTOR=${ACTOR:-fault-shell}
SESSION=visr-faults
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
TOKEN=""

get_token() {
  [ -n "$TOKEN" ] && return 0
  TOKEN=$(kubectl -n aiops get secret visr-auth -o jsonpath='{.data.operator-token}' 2>/dev/null | base64 -d 2>/dev/null)
  [ -n "$TOKEN" ] || { echo "STOP the Secret aiops/visr-auth has no operator token"; return 1; }
}

# 1 -> PS1, 4a -> PS4A. Only ids in the api catalogue pass.
to_sid() {
  local x
  x=$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')
  x="PS${x#PS}"
  curl -s -m 10 "$API/api/scenarios" | python3 -c '
import json, sys
ids = [s["id"] for s in json.load(sys.stdin) if s["id"] != "PS0"]
sys.exit(0 if sys.argv[1] in ids else 1)
' "$x" 2>/dev/null || return 1
  echo "$x"
}

status() {
  curl -s -m 10 "$API/api/scenarios" | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except ValueError:
    print("STOP the api does not answer at the catalogue"); sys.exit(1)
for s in rows:
    n = s["id"][2:].lower()
    st = {True: "ACTIVE", False: "-", None: "?"}[s.get("active")]
    if s["id"] == "PS0":
        st = "NOW" if s.get("active") else "-"
    print("  %-3s %-42s %s" % (n, s.get("name", "")[:42], st))
' || return 1
}

soak_running() { screen -ls 2>/dev/null | grep -q '\.visr-ps0[[:space:]]'; }

post() {   # post <path> <label>
  get_token || return 1
  local body code
  body=$(mktemp)
  code=$(curl -s -o "$body" -m 30 -w '%{http_code}' -X POST -H "X-Auth-Token: $TOKEN" \
    -H "X-Remote-User: $ACTOR" "$API$1")
  if [ "$code" = 200 ]; then
    echo "$2 $(date +%H:%M:%S)"
  else
    echo "ERROR $code $(python3 -c 'import json,sys
try: d = json.load(open(sys.argv[1])); print(d.get("detail") or d.get("status") or "")
except Exception: print("")' "$body")"
  fi
  rm -f "$body"
  [ "$code" = 200 ]
}

fire() {
  local sid
  sid=$(to_sid "${1:-}") || { echo "no scenario '${1:-}' (use 1, 2, 3, 4a, 4b, 5, 6)"; return 1; }
  if soak_running && [ "${FORCE:-0}" != 1 ]; then
    if [ -t 0 ]; then
      read -r -p "the PS0 soak runs (screen visr-ps0), and a fault ends it. Fire anyway? [y/N] " ok
      [ "$ok" = y ] || [ "$ok" = Y ] || { echo "not fired"; return 1; }
    else
      echo "STOP the PS0 soak runs (screen visr-ps0). Set FORCE=1 to fire anyway."; return 1
    fi
  fi
  post "/api/scenarios/$sid/trigger" "FIRED scenario ${sid#PS}"
}

reset() {
  if [ "${1:-}" = all ]; then post "/api/scenarios/reset-all" "RESET all"; return; fi
  local sid
  sid=$(to_sid "${1:-}") || { echo "no scenario '${1:-}' (use 1, 2, 3, 4a, 4b, 5, 6, all)"; return 1; }
  post "/api/scenarios/$sid/reset" "RESET scenario ${sid#PS}"
}

usage() {
  echo "  s | status      f <n> | fire <n>      r <n> | reset <n>      r all | reset all      q | quit"
}

run() {   # run <command> [arg]
  case "${1:-}" in
    s|status) status ;;
    f|fire) fire "${2:-}" ;;
    r|reset) reset "${2:-}" ;;
    h|help|"") usage ;;
    *) echo "unknown command '$1'"; usage; return 1 ;;
  esac
}

standby() {
  echo "VISR fault shell · $API · actor $ACTOR"
  status
  usage
  while read -e -r -p "faults> " cmd arg _; do
    case "$cmd" in
      q|quit|exit) break ;;
      "") continue ;;
      *) run "$cmd" "$arg" ;;
    esac
  done
}

# one command, then exit
if [ $# -gt 0 ] && [ "$1" != --standby ]; then
  run "$@"; exit $?
fi
# the standby shell, inside screen so that it survives a dropped SSH session
if [ "${1:-}" = --standby ] || [ -n "${STY:-}" ]; then
  standby; exit 0
fi
[ -t 0 ] || { echo "STOP the standby shell needs a terminal: use ssh -t"; exit 1; }
command -v screen >/dev/null || { standby; exit 0; }
if screen -ls 2>/dev/null | grep -q "\.$SESSION[[:space:]]"; then
  exec screen -d -r "$SESSION"
fi
exec screen -S "$SESSION" bash "$SELF" --standby
