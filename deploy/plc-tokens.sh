#!/usr/bin/env bash
# plc-tokens.sh: the device token Secret of every static PLC in deploy/fleet.yaml (LOG-100). No sudo.
# A token is HMAC-SHA256(enroll-key, PLC name), the same rule that deploy/resume.sh uses for
# plc-stamping and the api uses for a new PLC. The enroll key comes from Secret aiops/visr-fleet
# and never leaves this process. A PLC that already has its Secret is left as it is.
#
#   bash ~/Tata_InnoVent/deploy/plc-tokens.sh
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
PLCS=${PLCS:-"plc-stamping plc-utilities plc-machining plc-furnace"}

kubectl get ns fleet >/dev/null 2>&1 || kubectl create ns fleet >/dev/null || { echo "STOP no namespace fleet"; exit 1; }
umask 077
D=$(mktemp -d)
trap 'rm -rf "$D"' EXIT
kubectl -n aiops get secret visr-fleet -o jsonpath='{.data.enroll-key}' 2>/dev/null | base64 -d > "$D/enroll-key"
[ -s "$D/enroll-key" ] || { echo "STOP Secret aiops/visr-fleet has no enroll-key. Run deploy/resume.sh first."; exit 1; }
for plc in $PLCS; do
  if kubectl -n fleet get secret "$plc-token" >/dev/null 2>&1; then
    echo "$plc-token exists"
    continue
  fi
  printf '%s' "$plc" | openssl dgst -sha256 -hmac "$(cat "$D/enroll-key")" -r | cut -d' ' -f1 | tr -d '\n' > "$D/token"
  kubectl -n fleet create secret generic "$plc-token" --from-file=token="$D/token" >/dev/null \
    || { echo "STOP could not create Secret fleet/$plc-token"; exit 1; }
  echo "$plc-token created"
done
