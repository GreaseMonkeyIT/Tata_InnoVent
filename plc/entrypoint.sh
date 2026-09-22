#!/usr/bin/env bash
# Start OpenPLC, then upload, compile, and start plant_trips through the web UI.
# The web flow (login, upload-program, upload-program-action, compile-program, start_plc) is the same as
# in a browser (2F, LOG-036). If this flow fails, upload /program.st by hand in the web UI (NodePort 30081)
# after each pod start. Until then the plant-sim runs open-loop, which is the demo fail-open state.
#
# Web password (LOG-077). Each pod start gets a new container file system, so the user table in
# webserver/openplc.db holds the vendor default login again. Before the web server starts, this script sets
# the password of user openplc to the value from Secret plant/openplc-auth. The pod mounts that value at
# /etc/openplc-auth/password. So the vendor default never opens a session.
# When the Secret does not exist or its value is not usable, the script sets a random password for this
# pod only. The trip loop then starts as usual, but nobody can log in to the web UI.
# The password never goes into a command line or into the log.
set -u
cd /OpenPLC_v3
DB=/OpenPLC_v3/webserver/openplc.db
PW_FILE=/etc/openplc-auth/password
PW_RE='^[A-Za-z0-9._~-]{16,128}$'   # these characters need no escape in SQL text or in a form body
C=/tmp/cookies.txt

# 1. Get the password.
PW=""
[ -r "$PW_FILE" ] && PW=$(<"$PW_FILE")
if [[ $PW =~ $PW_RE ]]; then
  SRC=secret
else
  if [ -e "$PW_FILE" ]; then
    echo "[entrypoint] WARN the Secret value is not usable. It needs 16 to 128 characters from A-Z a-z 0-9 . _ ~ -"
  else
    echo "[entrypoint] WARN the pod has no password file from Secret plant/openplc-auth"
  fi
  PW=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  SRC=random
fi

# 2. Set the password before the web server starts. The SQL goes in through stdin, so the password stays
#    out of the process list. The answer "1 0" means: user openplc has the new password, and no user has
#    the vendor default password.
state=$(printf "UPDATE Users SET password = '%s' WHERE username = 'openplc';\nSELECT (SELECT count(*) FROM Users WHERE username = 'openplc' AND password = '%s') || ' ' || (SELECT count(*) FROM Users WHERE password = 'openplc');\n" "$PW" "$PW" \
  | sqlite3 -batch "$DB" 2>/dev/null)
if [ "$state" = "1 0" ] && [ "$SRC" = secret ]; then
  LOGIN_PW=$PW
  echo "[entrypoint] PASS user openplc has the password from Secret plant/openplc-auth. The vendor default is off."
elif [ "$state" = "1 0" ]; then
  LOGIN_PW=$PW
  echo "[entrypoint] WARN user openplc has a random password for this pod only. Nobody can log in to the web UI."
  echo "[entrypoint]      Run deploy/openplc-auth.sh, then restart deploy/openplc in ns plant."
else
  # The trip loop comes first: log in with the vendor default, which the table still holds.
  LOGIN_PW=openplc
  echo "[entrypoint] FAIL the password change in webserver/openplc.db failed. The vendor default login stays on."
fi
unset PW

./start_openplc.sh &
OPENPLC_PID=$!

echo "[entrypoint] waiting for the OpenPLC web UI..."
for i in $(seq 1 60); do
  curl -sf -o /dev/null http://localhost:8080/login && break
  sleep 2
done

# The form body goes in through stdin, so the password stays out of the process list.
# The web UI answers 302 (to the dashboard) to a good login. It answers 200 (the login page) to a bad one.
code=$(printf 'username=openplc&password=%s' "$LOGIN_PW" \
  | curl -s -c "$C" -o /dev/null -w '%{http_code}' --data-binary @- http://localhost:8080/login)
unset LOGIN_PW
[ "$code" = 302 ] || echo "[entrypoint] FAIL the web login answered $code, not 302. The program upload will fail."

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
echo "[entrypoint] runtime start requested. The Modbus slave listens on port 502 (on by default)."

# Check the result. The web dashboard shows the program name and the runtime state.
ok=0
for i in $(seq 1 10); do
  curl -s -b "$C" http://localhost:8080/dashboard -o /tmp/dashboard.html || true
  if grep -q 'Program:</b> plant_trips</p>' /tmp/dashboard.html && grep -q 'Running</font>' /tmp/dashboard.html; then
    ok=1
    break
  fi
  sleep 2
done
if [ "$ok" = 1 ]; then
  echo "[entrypoint] PASS the runtime runs plant_trips"
else
  echo "[entrypoint] FAIL the dashboard does not show plant_trips in Running. Read /tmp/compile.html in the pod."
fi
# The image has the REST API off (plc/rest-off.sh, LOG-077), so nothing may answer on port 8443.
rest=$(curl -sk -o /dev/null -m 5 -w '%{http_code}' https://localhost:8443/api/ping)
if [ "$rest" = 000 ]; then
  echo "[entrypoint] PASS nothing answers on the REST API port 8443"
else
  echo "[entrypoint] FAIL the REST API answers $rest on port 8443"
fi
rm -f "$C"
echo "[entrypoint] bring-up finished"

wait "$OPENPLC_PID"
