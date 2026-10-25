#!/usr/bin/env bash
# Build and run the console preview on forge (LOG-121, LOG-122).
#
#   bash ~/VISR_Overhaul/deploy/console-preview.sh          build the console, (re)start the preview
#   LIVE=1 bash ~/VISR_Overhaul/deploy/console-preview.sh   the same on the live plant (2026-10-22)
#   STOP=1 bash ~/VISR_Overhaul/deploy/console-preview.sh   stop and remove the preview
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
#
# LIVE=1 (live/README.md): the plant service (live/plant_service.py --floor) and the engine service
# (live/engine_service.py with the newest eval/results/*/live-pack.pkl, or VISR_PACK) run on forge
# itself, from the venv VISR_PYTHON (default ~/visr-venv), in the screen sessions visr-live-plant
# and visr-live-engine (logs /var/tmp/visr-live-*.log). They listen on the docker0 address only
# (172.17.0.1, which nginx.preview.conf names), so only the containers and forge reach them. The
# replay exporter then serves the live plant (VISR_LIVE). VISR_SEED (default 60000, a validation
# day), VISR_SPEED (default 1) and VISR_FAULTS ("F6:0.5:900 F13:0.4:5400", faults planned after the
# warm-up) set the run. A fault during the run: curl -X POST http://172.17.0.1:9110/fault -d
# '{"fid": "F6", "severity": 0.5}' on forge.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
NET=visr-preview
PORT="${PORT:-3121}"
GRAFANA_IMAGE="${GRAFANA_IMAGE:-visr-preview/grafana:k3s}"
NODE_IMAGE="${NODE_IMAGE:-node:22-slim}"
ALL="visr-console-preview visr-grafana visr-replay"
LIVE="${LIVE:-0}"
PY="${VISR_PYTHON:-$HOME/visr-venv/bin/python}"

stop_live() {
  screen -S visr-live-plant -X quit >/dev/null 2>&1 || true
  screen -S visr-live-engine -X quit >/dev/null 2>&1 || true
}

if [ "${STOP:-0}" = 1 ]; then
  docker rm -f $ALL >/dev/null 2>&1 || true
  stop_live
  echo "stopped: $ALL and the live services"
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
if [ "$LIVE" = 1 ]; then
  HOSTIP="$(ip -4 -o addr show docker0 | awk '{print $4}' | cut -d/ -f1)"
  [ "$HOSTIP" = 172.17.0.1 ] || { echo "docker0 is '$HOSTIP'; nginx.preview.conf expects 172.17.0.1" >&2; exit 1; }
  PACK="${VISR_PACK:-$(ls -t "$HERE"/eval/results/*/live-pack.pkl 2>/dev/null | head -n1)}"
  [ -f "$PACK" ] || { echo "no live-pack.pkl: python -m live.engine pack eval/results/<campaign>" >&2; exit 1; }
  [ -x "$PY" ] || { echo "no Python at $PY (VISR_PYTHON)" >&2; exit 1; }
fi

TAG="${TAG:-$(date +%Y%m%d-%H%M)}"
IMAGE="visr-overhaul/console:${TAG}"
docker build -f "$HERE/dashboard/Dockerfile.preview" -t "$IMAGE" "$HERE/dashboard"

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
docker rm -f $ALL >/dev/null 2>&1 || true

SPEED="${VISR_SPEED:-1}"
if [ "$LIVE" = 1 ]; then
  stop_live
  FAULTS=""
  for f in ${VISR_FAULTS:-}; do FAULTS="$FAULTS --fault $f"; done
  screen -dmS visr-live-plant bash -c "cd '$HERE' && exec '$PY' -m live.plant_service --floor --host $HOSTIP --port 9110 \
    --seed ${VISR_SEED:-60000} --speed $SPEED $FAULTS >> /var/tmp/visr-live-plant.log 2>&1"
  screen -dmS visr-live-engine bash -c "cd '$HERE' && exec '$PY' -m live.engine_service --pack '$PACK' \
    --plant http://$HOSTIP:9110 --host $HOSTIP --port 9111 >> /var/tmp/visr-live-engine.log 2>&1"
  REPLAY_ENV="-e VISR_LIVE=http://$HOSTIP:9110"
else
  # the replay clock: now is VISR_START seconds into the recording (default 0; the mock incident
  # starts at 3600 s and recovers at 7200 s). The console reads the clock from /api/clock.
  ANCHOR=$(( $(date +%s) - ${VISR_START:-0} / SPEED ))
  REPLAY_ENV="-e VISR_ANCHOR=$ANCHOR -e VISR_SPEED=$SPEED -e VISR_MOCK=${VISR_MOCK:-1}"
fi
docker run -d --name visr-replay --network "$NET" $REPLAY_ENV \
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
# the live exporter answers once the plant has warmed up (9 h of plant time at full speed)
CHECKS="/healthz /grafana/api/health /api/clock"
[ "$LIVE" = 1 ] && CHECKS="/healthz /grafana/api/health /api/live/plant/healthz /api/live/engine/healthz /api/clock"
for i in $(seq 1 420); do
  all=1; for p in $CHECKS; do ok "$p" || all=0; done
  [ $all = 1 ] && break; sleep 1
done
for p in $CHECKS; do
  ok "$p" || { echo "no answer on $p: docker logs of the preview containers, /var/tmp/visr-live-*.log" >&2; exit 1; }
done
echo "console preview: http://${ADDR}:${PORT}/   (image ${IMAGE}, Grafana ${GRAFANA_IMAGE}$( [ "$LIVE" = 1 ] && echo ", live plant seed ${VISR_SEED:-60000}, pack $(basename "$(dirname "$PACK")")"))"
