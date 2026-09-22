#!/bin/sh
# rest-off.sh: turn off the OpenPLC REST API (HTTPS, port 8443) in webserver.py (LOG-077). The image build
# runs this script. Upstream starts the REST API in a second thread next to the web UI. After each pod start
# the REST API has no user, and its first caller can make one without a login. That user can then stop the
# PLC or replace the program. This project does not use the REST API. The web UI (port 8080) stays the only
# way to change the program, and its login needs the password from Secret plant/openplc-auth.
# Safe to run again. The script stops the build when webserver.py does not look as expected.
set -eu
F=/OpenPLC_v3/webserver/webserver.py
START='    threading.Thread(target=run_https).start()'

n=$(grep -cxF "$START" "$F" || true)
case "$n" in
  1) sed -i '/^    threading\.Thread(target=run_https)\.start()$/d' "$F" ;;
  0) echo "rest-off: $F has no REST API start line (an earlier run removed it)" ;;
  *) echo "rest-off: $F has $n REST API start lines. Expected 1."; exit 1 ;;
esac
# After the edit, run_https may appear only in its own definition. Any other use can start the REST API.
if [ "$(grep -c 'run_https' "$F")" != 1 ]; then
  echo "rest-off: $F still uses run_https outside its definition"; exit 1
fi
grep -qxF '    threading.Thread(target=run_http).start()' "$F" || { echo "rest-off: $F has no web UI start line"; exit 1; }
python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$F"
echo "rest-off: the REST API is off, and the web UI starts as before"
