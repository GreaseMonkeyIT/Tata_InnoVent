#!/usr/bin/env bash
# historian-auth.sh: the historian database password lives in Secret plant/historian-auth (LOG-076).
# deploy/golive.sh runs this script before it applies the manifests. No sudo. Safe to run again.
#   bash ~/Tata_InnoVent/deploy/historian-auth.sh
# When the Secret exists, the script changes nothing.
# When the Secret is missing, the script makes a random password, sets it in a running historian
# first, and then writes the Secret. POSTGRES_PASSWORD acts only on an empty data folder, so a
# running database keeps its old password until this script changes it.
# To set a new password: delete the Secret, run this script, then restart deploy/tag-server in ns plant.
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
NS=plant
SECRET=historian-auth
POD=historian-db-0

if kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
  echo "Secret $NS/$SECRET exists and stays unchanged"
  exit 0
fi
kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS"

umask 077
D=$(mktemp -d)
trap 'rm -rf "$D"' EXIT
openssl rand -hex 24 | tr -d '\n' > "$D/password"

if kubectl -n "$NS" get pod "$POD" >/dev/null 2>&1; then
  if ! kubectl -n "$NS" wait --for=condition=Ready "pod/$POD" --timeout=180s >/dev/null; then
    echo "the historian pod $NS/$POD is not Ready. The Secret was not written."
    exit 1
  fi
  # The SQL goes in through stdin, so the password stays out of the process list.
  # psql connects through the local socket, which the postgres image trusts inside the pod.
  if ! printf "ALTER USER postgres WITH PASSWORD '%s';\n" "$(cat "$D/password")" \
      | kubectl -n "$NS" exec -i "$POD" -- psql -v ON_ERROR_STOP=1 -q -U postgres >/dev/null; then
    echo "the password change in $NS/$POD failed. The Secret was not written."
    exit 1
  fi
  echo "the running historian has the new password"
fi

kubectl -n "$NS" create secret generic "$SECRET" --from-file=password="$D/password" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "Secret $NS/$SECRET written"
