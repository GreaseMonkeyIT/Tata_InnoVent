# The live plant service and the live engine service, one image for both (live/README.md, step 4).
# LIVE=1 deploy/console-preview.sh builds it on forge as visr-overhaul/live:<tag> and runs it twice on
# the preview network:
#   visr-live-plant    python -m live.plant_service --floor --host 0.0.0.0 ...
#   visr-live-engine   python -m live.engine_service --pack /campaign/live-pack.pkl ...
# Build from the repository root (live/Dockerfile.dockerignore keeps the context to the code):
#   docker build -f live/Dockerfile -t visr-overhaul/live:<tag> .
# The image holds the code of live/, eval/, plant/model, plant/floor and twin/ and no data: the
# weather file (data/weather/, gitignored) and the campaign folder of the pack come in as read-only
# mounts. It never takes an skn/* name or the tag v0.1 (the proof-of-concept stack on forge).
#
# Python 3.11, as in CI (python:3.11-slim of 2026-10-28: Python 3.11.18, Debian 13.7). Every package
# is pinned: the pins of correlation/requirements.txt, and numba with its llvmlite (the newest numba
# for numpy 2.4.6 on Python 3.11). BASE overrides the base image, for a mirror of the same digest.
ARG BASE=python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
FROM ${BASE}

# numba writes its compiled kernels to NUMBA_CACHE_DIR: the code is read-only for the service user
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NUMBA_CACHE_DIR=/tmp/numba
COPY correlation/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt numba==0.68.1 llvmlite==0.50.0 \
 && rm /tmp/requirements.txt
RUN groupadd --system --gid 10001 visr \
 && useradd --system --uid 10001 --gid visr --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin visr

WORKDIR /srv/visr
COPY live/*.py live/
COPY eval/*.py eval/
COPY twin/*.py twin/
COPY plant/model/ plant/model/
COPY plant/floor/ plant/floor/
RUN python -m compileall -q live eval twin plant

USER visr
EXPOSE 9110 9111
# GET /healthz of the service in the container: 9110, the plant (the engine container sets 9111)
ENV VISR_HEALTH_PORT=9110
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=3 CMD ["python", "-c", \
  "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['VISR_HEALTH_PORT'], timeout=2)"]
ENTRYPOINT ["python", "-m"]
CMD ["live.plant_service", "--host", "0.0.0.0"]
