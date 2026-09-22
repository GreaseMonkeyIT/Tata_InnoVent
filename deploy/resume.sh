#!/usr/bin/env bash
# resume.sh: the operator part of bringing the paused box back (PIVOT_SETUP.md "Resume after a pause").
# Run it on the box as the normal user, after the reboot:
#   bash ~/Tata_InnoVent/deploy/resume.sh
# It asks for the sudo password once, and for the viewer and operator dashboard passwords when the
# dashboard Secrets do not exist yet. Every step is safe to run again. Next step: deploy/golive.sh.
set -euo pipefail
export KUBECONFIG="$HOME/.kube/config"

echo "== 1/5 local image registry and the k3s mirror file"
if ! docker ps --format '{{.Names}}' | grep -qx visr-registry; then
  docker volume create visr-registry >/dev/null
  docker start visr-registry >/dev/null 2>&1 || docker run -d --restart=always --name visr-registry \
    -p 127.0.0.1:5000:5000 -v visr-registry:/var/lib/registry registry:2 >/dev/null
fi
was_active=$(systemctl is-active k3s || true)
printf 'mirrors:\n  "localhost:5000":\n    endpoint:\n      - "http://localhost:5000"\n' \
  | sudo tee /etc/rancher/k3s/registries.yaml >/dev/null
echo "registries.yaml written"

echo "== 2/5 start k3s"
if [ "$was_active" = "active" ]; then
  sudo systemctl restart k3s                # k3s reads registries.yaml only when it starts
else
  sudo systemctl enable --now k3s
fi

echo "== 3/5 wait for the node (up to 5 min)"
for _ in $(seq 1 60); do
  if kubectl get nodes 2>/dev/null | grep -q " Ready"; then break; fi
  sleep 5
done
kubectl get nodes

echo "== 4/5 dashboard Secrets (2E): visr-auth and visr-tls in aiops"
kubectl get ns aiops >/dev/null 2>&1 || kubectl create ns aiops
if kubectl -n aiops get secret visr-auth >/dev/null 2>&1 && kubectl -n aiops get secret visr-tls >/dev/null 2>&1; then
  echo "both Secrets exist and stay unchanged (delete them to set new passwords)"
else
  (
    umask 077
    D=$(mktemp -d)
    trap 'rm -rf "$D"' EXIT
    VP=""; OP=""
    while [ -z "$VP" ]; do read -rsp 'viewer password: ' VP; echo; done
    while [ -z "$OP" ]; do read -rsp 'operator password: ' OP; echo; done
    printf 'viewer:%s\noperator:%s\n' \
      "$(printf '%s' "$VP" | openssl passwd -6 -stdin)" \
      "$(printf '%s' "$OP" | openssl passwd -6 -stdin)" > "$D/htpasswd"
    unset VP OP
    openssl rand -hex 24 | tr -d '\n' > "$D/token"
    openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj "/CN=visr.local" \
      -keyout "$D/tls.key" -out "$D/tls.crt" 2>/dev/null
    kubectl -n aiops create secret generic visr-auth --from-file=htpasswd="$D/htpasswd" \
      --from-file=operator-token="$D/token" --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n aiops create secret tls visr-tls --cert="$D/tls.crt" --key="$D/tls.key" \
      --dry-run=client -o yaml | kubectl apply -f -
  )
fi

echo "== 5/5 fleet Secrets (2H): visr-fleet in aiops and plant, plc-stamping-token in fleet"
for ns in plant fleet; do kubectl get ns "$ns" >/dev/null 2>&1 || kubectl create ns "$ns"; done
(
  umask 077
  D=$(mktemp -d)
  trap 'rm -rf "$D"' EXIT
  if kubectl -n aiops get secret visr-fleet >/dev/null 2>&1; then
    kubectl -n aiops get secret visr-fleet -o jsonpath='{.data.enroll-key}' | base64 -d > "$D/enroll-key"
    kubectl -n aiops get secret visr-fleet -o jsonpath='{.data.scada-write-token}' | base64 -d > "$D/scada-write-token"
    echo "visr-fleet exists, its keys stay unchanged"
  else
    openssl rand -hex 32 | tr -d '\n' > "$D/enroll-key"
    openssl rand -hex 24 | tr -d '\n' > "$D/scada-write-token"
  fi
  for ns in aiops plant; do
    kubectl -n "$ns" create secret generic visr-fleet --from-file="$D/enroll-key" \
      --from-file="$D/scada-write-token" --dry-run=client -o yaml | kubectl apply -f -
  done
  # the base PLC token = HMAC-SHA256(enroll-key, plc name), the same rule the api uses for new PLCs
  printf '%s' plc-stamping | openssl dgst -sha256 -hmac "$(cat "$D/enroll-key")" -r | cut -d' ' -f1 \
    | tr -d '\n' > "$D/token"
  kubectl -n fleet create secret generic plc-stamping-token --from-file=token="$D/token" \
    --dry-run=client -o yaml | kubectl apply -f -
)

echo
echo "resume.sh finished. k3s runs, the registry mirror is set, and every Secret exists."
if [ "${GOLIVE:-1}" = "1" ]; then
  # go live at once, detached, so a closed terminal or SSH session does not stop it (linger is off)
  screen -dmS visr-golive bash -c "bash '$HOME/Tata_InnoVent/deploy/golive.sh' > /var/tmp/visr-golive.log 2>&1"
  echo "golive.sh started in screen session 'visr-golive'. Follow it: tail -f /var/tmp/visr-golive.log"
else
  echo "Next: bash ~/Tata_InnoVent/deploy/golive.sh (no sudo)."
fi
