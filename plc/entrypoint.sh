#!/usr/bin/env bash
# Start OpenPLC, then idempotently upload + compile + start plant_trips via the web API.
# The web flow (login -> upload-program -> upload-program-action -> compile-program -> start_plc)
# mirrors what the browser does; endpoint names are from OpenPLC's webserver and are the
# BOX-VERIFY step of 2F. If this automation fails, the fallback is one manual upload of
# /program.st via the web UI (NodePort 30081, login openplc/openplc) per pod restart —
# and until then the plant-sim simply runs open-loop (fails safe for the demo).
set -u
cd /OpenPLC_v3
./start_openplc.sh &
OPENPLC_PID=$!

echo "[entrypoint] waiting for the OpenPLC web UI..."
for i in $(seq 1 60); do
  curl -sf -o /dev/null http://localhost:8080/login && break
  sleep 2
done

C=/tmp/cookies.txt
curl -s -c "$C" -d "username=openplc&password=openplc" http://localhost:8080/login -o /dev/null || true

# upload the ST source. The server stores it under st_files/ with a GENERATED name — read that
# back from the returned page's hidden prog_file field (compiling the SUBMITTED name was the
# box-verify failure: FileNotFoundError on ./st_files/program.st, LOG-036). Belt-and-suspenders:
# also place the file under both names so the compile target exists even if the scrape misses.
curl -s -b "$C" -F "file=@/program.st;filename=program.st" \
     http://localhost:8080/upload-program -o /tmp/upload.html || true
STFILE=$(tr '>' '\n' </tmp/upload.html | grep 'prog_file' | grep -oE 'value="[^"]+"' | head -1 | cut -d'"' -f2)
STFILE=${STFILE:-program.st}
cp /program.st "/OpenPLC_v3/webserver/st_files/$STFILE" 2>/dev/null || true
cp /program.st /OpenPLC_v3/webserver/st_files/program.st 2>/dev/null || true
curl -s -b "$C" \
     -d "prog_name=plant_trips&prog_descr=2F trip interlocks&prog_file=$STFILE&epoch_time=$(date +%s)" \
     http://localhost:8080/upload-program-action -o /tmp/action.html || true

echo "[entrypoint] compiling $STFILE..."
curl -s -b "$C" "http://localhost:8080/compile-program?file=$STFILE" -o /tmp/compile.html || true
for i in $(seq 1 30); do
  curl -s -b "$C" http://localhost:8080/compilation-logs | grep -qi "compilation finished" && break
  sleep 2
done

curl -s -b "$C" http://localhost:8080/start_plc -o /dev/null || true
echo "[entrypoint] runtime start requested; Modbus slave on :502 (default-enabled)."

wait "$OPENPLC_PID"
