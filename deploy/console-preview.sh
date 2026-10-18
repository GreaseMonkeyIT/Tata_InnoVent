#!/usr/bin/env bash
# Build and run the console preview on forge (LOG-121).
#
#   bash ~/VISR_Overhaul/deploy/console-preview.sh          build a new image, replace the container
#   STOP=1 bash ~/VISR_Overhaul/deploy/console-preview.sh   stop and remove the container
#
# The script builds the image visr-overhaul/console:<date-time> from dashboard/ (Dockerfile.preview)
# and replaces the container visr-console-preview. The container serves the console on port 3121 of
# the Tailscale address of forge only (not on the LAN). It has no restart policy, so it does not
# start again after a reboot. The script does not touch k3s, the local registry or any image or
# namespace of the main stack (skn/*).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
NAME=visr-console-preview
PORT="${PORT:-3121}"

if [ "${STOP:-0}" = 1 ]; then
  docker rm -f "$NAME" >/dev/null 2>&1 && echo "stopped $NAME" || echo "$NAME is not running"
  exit 0
fi

ADDR="${ADDR:-$(tailscale ip -4 | head -n1)}"
[ -n "$ADDR" ] || { echo "no Tailscale address: is tailscaled up?" >&2; exit 1; }
[ -f "$HERE/dashboard/public/floor/replay.json" ] || { echo "no plant recording: run plant/floor/replay.py first" >&2; exit 1; }

TAG="${TAG:-$(date +%Y%m%d-%H%M)}"
IMAGE="visr-overhaul/console:${TAG}"
docker build -f "$HERE/dashboard/Dockerfile.preview" -t "$IMAGE" "$HERE/dashboard"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "${ADDR}:${PORT}:80" "$IMAGE" >/dev/null
for i in $(seq 1 20); do
  curl -fsS "http://${ADDR}:${PORT}/healthz" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS "http://${ADDR}:${PORT}/healthz" >/dev/null || { echo "the container does not answer: docker logs $NAME" >&2; exit 1; }
echo "console preview: http://${ADDR}:${PORT}/   (image ${IMAGE})"
