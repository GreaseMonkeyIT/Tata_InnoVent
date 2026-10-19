#!/usr/bin/env bash
# Build and run the console preview on forge (LOG-121, LOG-122).
#
#   bash ~/VISR_Overhaul/deploy/console-preview.sh          build the console, (re)start the preview
#   STOP=1 bash ~/VISR_Overhaul/deploy/console-preview.sh   stop and remove the preview containers
#
# Three containers on the Docker network visr-preview:
#   visr-replay   the plant recording played on the wall clock (deploy/preview/replay-exporter.mjs,
#                 on node:22-slim, with dashboard/app and the recording mounted read-only). It is
#                 Grafana's datasource: it answers the Prometheus query API for each panel's window.
#   visr-grafana  Grafana with the console panels (deploy/preview/grafana/), anonymous viewer,
#                 embedding on, served from /grafana/
#   visr-console-preview  the console (dashboard/Dockerfile.preview); its nginx proxies /grafana/
#                 and /api/clock on the same origin
# Only the console publishes a port: 3121 on the Tailscale address of forge (not on the LAN). The
# containers have no restart policy. The Grafana image comes from the paused k3s stack
# (deploy/k3s-images-to-docker.py, run once with sudo); GRAFANA_IMAGE overrides it. The script does
# not touch k3s, the local registry or any image or namespace of skn/*.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
NET=visr-preview
PORT="${PORT:-3121}"
GRAFANA_IMAGE="${GRAFANA_IMAGE:-visr-preview/grafana:k3s}"
NODE_IMAGE="${NODE_IMAGE:-node:22-slim}"
ALL="visr-console-preview visr-grafana visr-replay"

if [ "${STOP:-0}" = 1 ]; then
  docker rm -f $ALL >/dev/null 2>&1 || true
  echo "stopped: $ALL"
  exit 0
fi

ADDR="${ADDR:-$(tailscale ip -4 | head -n1)}"
[ -n "$ADDR" ] || { echo "no Tailscale address: is tailscaled up?" >&2; exit 1; }
REC="$HERE/dashboard/public/floor/replay.json"
[ -f "$REC" ] || { echo "no plant recording: run plant/floor/replay.py first" >&2; exit 1; }
for img in "$GRAFANA_IMAGE" "$NODE_IMAGE"; do
  docker image inspect "$img" >/dev/null 2>&1 || {
    echo "no image $img: run 'sudo python3 $HERE/deploy/k3s-images-to-docker.py' once (Grafana)" >&2; exit 1; }
done

TAG="${TAG:-$(date +%Y%m%d-%H%M)}"
IMAGE="visr-overhaul/console:${TAG}"
docker build -f "$HERE/dashboard/Dockerfile.preview" -t "$IMAGE" "$HERE/dashboard"

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
docker rm -f $ALL >/dev/null 2>&1 || true

# the replay clock: now is VISR_START seconds into the recording (default 0; the mock incident
# starts at 3600 s and recovers at 7200 s). The console reads the clock from /api/clock.
SPEED="${VISR_SPEED:-1}"
ANCHOR=$(( $(date +%s) - ${VISR_START:-0} / SPEED ))
docker run -d --name visr-replay --network "$NET" \
  -e VISR_ANCHOR="$ANCHOR" -e VISR_SPEED="$SPEED" -e VISR_MOCK="${VISR_MOCK:-1}" \
  -v "$HERE/dashboard/app:/app:ro" -v "$REC:/data/replay.json:ro" \
  -v "$HERE/deploy/preview/replay-exporter.mjs:/srv/replay-exporter.mjs:ro" \
  "$NODE_IMAGE" node --no-warnings /srv/replay-exporter.mjs >/dev/null
# a random admin password that nobody keeps: the console needs only the anonymous viewer
docker run -d --name visr-grafana --network "$NET" \
  -e GF_SERVER_ROOT_URL="http://${ADDR}:${PORT}/grafana/" -e GF_SERVER_SERVE_FROM_SUB_PATH=true \
  -e GF_AUTH_ANONYMOUS_ENABLED=true -e GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer -e GF_SECURITY_ALLOW_EMBEDDING=true \
  -e GF_SECURITY_ADMIN_PASSWORD="$(head -c 24 /dev/urandom | base64)" \
  -e GF_ANALYTICS_REPORTING_ENABLED=false -e GF_ANALYTICS_CHECK_FOR_UPDATES=false \
  -e GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false -e GF_NEWS_NEWS_FEED_ENABLED=false \
  -e GF_DASHBOARDS_MIN_REFRESH_INTERVAL=5s \
  -v "$HERE/deploy/preview/grafana/provisioning:/etc/grafana/provisioning:ro" \
  -v "$HERE/deploy/preview/grafana/dashboards:/var/lib/grafana/dashboards:ro" \
  "$GRAFANA_IMAGE" >/dev/null
docker run -d --name visr-console-preview --network "$NET" -p "${ADDR}:${PORT}:80" "$IMAGE" >/dev/null

ok() { curl -fsS -o /dev/null "http://${ADDR}:${PORT}$1" 2>/dev/null; }
for i in $(seq 1 90); do ok /healthz && ok /api/clock && ok /grafana/api/health && break; sleep 1; done
for p in /healthz /api/clock /grafana/api/health; do
  ok "$p" || { echo "no answer on $p: docker logs of the preview containers" >&2; exit 1; }
done
echo "console preview: http://${ADDR}:${PORT}/   (image ${IMAGE}, Grafana ${GRAFANA_IMAGE})"
