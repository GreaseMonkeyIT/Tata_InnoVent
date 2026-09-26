#!/usr/bin/env bash
# netpol.sh: the network policies of the VISR namespaces (deploy/netpol.yaml, LOG-095).
# Run it on the box as the normal user (no sudo):
#   bash ~/Tata_InnoVent/deploy/netpol.sh verify   # read-only: tries every allowed path and a set of denied ones
#   bash ~/Tata_InnoVent/deploy/netpol.sh apply    # apply, verify, and remove them again if a check fails
#   bash ~/Tata_InnoVent/deploy/netpol.sh remove   # delete every policy of deploy/netpol.yaml
#
# The allowed paths are the flows that Caretta saw on forge on 2026-09-26. A denied path must time out.
# apply stops while a soak, a proof run, or a verification run is active: a policy change must not land
# in the middle of a measurement. Never apply during a recording.
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
HERE="$(cd "$(dirname "$0")" && pwd)"
FAILS=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; FAILS=$((FAILS + 1)); }
die() { echo "STOP $*"; exit 1; }

# can <ns> <deploy> <host> <port>: a TCP connect from inside the pod, 3 s timeout (python in the image)
can() {
  kubectl -n "$1" exec "deploy/$2" -- python3 -c \
    "import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), 3)" "$3" "$4" >/dev/null 2>&1
}
allow() { can "$@" && pass "$2 -> $3:$4" || fail "$2 -> $3:$4 is blocked, and it must pass"; }
deny()  { can "$@" && fail "$2 -> $3:$4 passes, and it must be blocked" || pass "$2 -x $3:$4 blocked"; }

verify() {
  local P=plant.svc.cluster.local F=fleet.svc.cluster.local A=aiops.svc.cluster.local
  allow aiops api plant-sim.$P 9200
  allow aiops api tag-server.$P 9300
  allow aiops api correlation-engine.$A 9100
  allow aiops api aggregator.$A 9000
  allow aiops api plc-stamping.$F 8080
  allow aiops correlation-engine aggregator.$A 9000
  allow aiops correlation-engine plant-sim.$P 9200
  allow aiops correlation-engine tag-server.$P 9300
  allow plant tag-server plc-stamping.$F 102
  allow plant tag-server openplc.$P 502
  allow plant tag-server historian-db.$P 5432
  allow plant plant-sim plc-stamping.$F 5020
  allow plant plant-sim openplc.$P 502
  allow fleet plc-stamping tag-server.$P 9300
  deny  aiops api historian-db.$P 5432
  deny  plant plant-sim historian-db.$P 5432
  deny  plant plant-sim plc-stamping.$F 102
  deny  fleet plc-stamping openplc.$P 502
  deny  plant plant-sim 1.1.1.1 443                    # no internet from the plant zone
  deny  plant plant-sim tag-server.$P 9300             # same-namespace peers must hold too (kube-router)
  deny  aiops correlation-engine api.$A 8088
  local api
  api=$(kubectl -n aiops get svc api -o jsonpath='{.spec.clusterIP}' 2>/dev/null)
  if curl -s -m 8 "http://$api:8088/api/health" | grep -q '"auth"'; then
    pass "the box reaches the api at its ClusterIP"
  else
    fail "the box does not reach the api at http://$api:8088"
  fi
  if kubectl get networkpolicy -A --no-headers 2>/dev/null | grep -q .; then
    echo "policies in force: $(kubectl get networkpolicy -A --no-headers | wc -l)"
  else
    echo "no policies in force: the deny checks above passed only if something else blocks them"
  fi
  echo "verify: $FAILS failed"
  return "$FAILS"
}

case "${1:-verify}" in
  verify)
    verify
    ;;
  apply)
    screen -ls 2>/dev/null | grep -Eq '\.visr-(ps0|proof|verify)[[:space:]]' \
      && die "a soak, a proof run, or a verification run is active (screen). Wait for it to end."
    kubectl apply -f "$HERE/netpol.yaml" || die "kubectl apply failed"
    sleep 10                                          # the policy controller programs the rules
    if verify; then
      echo "DONE the policies are in force"
    else
      echo "ROLLBACK a check failed, so the policies are removed again"
      kubectl delete -f "$HERE/netpol.yaml" --ignore-not-found
      exit 1
    fi
    ;;
  remove)
    kubectl delete -f "$HERE/netpol.yaml" --ignore-not-found
    ;;
  *)
    die "usage: netpol.sh verify|apply|remove"
    ;;
esac
