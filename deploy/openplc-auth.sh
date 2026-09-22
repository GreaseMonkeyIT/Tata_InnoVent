#!/usr/bin/env bash
# openplc-auth.sh: the OpenPLC web UI password lives in Secret plant/openplc-auth (LOG-077).
# deploy/golive.sh runs this script before it applies the manifests. No sudo. Safe to run again.
#   bash ~/Tata_InnoVent/deploy/openplc-auth.sh
# When the Secret exists, the script changes nothing.
# When the Secret does not exist, the script makes a random password and writes the Secret. The running pod
# does not change. plc/entrypoint.sh sets the password in the OpenPLC user table at the next pod start.
# To read the password for a web UI login as user openplc:
#   kubectl -n plant get secret openplc-auth -o jsonpath='{.data.password}' | base64 -d; echo
# To set a new password: delete the Secret, run this script, then restart deploy/openplc in ns plant.
# A restart opens the trip loop for about one minute, so do it only while no fault runs.
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
NS=plant
SECRET=openplc-auth

if kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
  echo "Secret $NS/$SECRET exists and stays unchanged"
  exit 0
fi
kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS"

umask 077
D=$(mktemp -d)
trap 'rm -rf "$D"' EXIT
# 48 hex characters. plc/entrypoint.sh accepts 16 to 128 characters from A-Z a-z 0-9 . _ ~ -
openssl rand -hex 24 | tr -d '\n' > "$D/password"
kubectl -n "$NS" create secret generic "$SECRET" --from-file=password="$D/password" >/dev/null
echo "Secret $NS/$SECRET written. The OpenPLC pod uses it from its next start."
