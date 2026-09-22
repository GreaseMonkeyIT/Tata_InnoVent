#!/usr/bin/env bash
# openplc-rollout.sh: put the LOG-077 OpenPLC image on the box, check it, or put the old image back.
# No sudo. Run it on the box as the normal user:
#   bash ~/Tata_InnoVent/deploy/openplc-rollout.sh test       # a build and a throwaway container only
#   bash ~/Tata_InnoVent/deploy/openplc-rollout.sh deploy     # test, then roll the new image onto deploy/openplc
#   bash ~/Tata_InnoVent/deploy/openplc-rollout.sh rollback   # put the image from before LOG-077 back
#   bash ~/Tata_InnoVent/deploy/openplc-rollout.sh probe      # read-only check of the running pod
# Use screen for deploy, so a closed SSH session does not stop it:
#   screen -dmS visr-openplc bash -c 'bash ~/Tata_InnoVent/deploy/openplc-rollout.sh deploy > /var/tmp/visr-openplc.log 2>&1'
#
# The LOG-077 image: the web UI takes only the password from Secret plant/openplc-auth, and the REST API
# (port 8443) is off. The OpenPLC runtime does not change.
# test: build skn/openplc:log077 on top of the image that runs now. That takes seconds and needs no source
#   build. Run the image in a throwaway container with no network and a throwaway password. Check the two
#   logins, the program, and port 8443. k3s and the registry do not change.
# deploy: stop while a soak or a proof run is active, a fault is active, or a machine has a latched trip.
#   A PLC restart opens the trip loop for about one minute and clears every latched trip. Then run test, keep the
#   running image as skn/openplc:pre-log077, write the Secret, push the new image as v0.1, apply
#   deploy/openplc.yaml, and restart deploy/openplc. When the new pod does not close the trip loop with
#   plant_trips, the script runs rollback.
# rollback: push skn/openplc:pre-log077 as v0.1 and restart deploy/openplc. The Secret and the manifest
#   stay. The old entrypoint does not read the Secret, so its web login is the vendor default again.
# probe: print four words from inside the running pod (see PROBE). deploy/golive.sh step 5 reads them.
#   The exit status is 0 only for "200 302 plant_trips 000". An image from before LOG-077 gives "302 200 - 401".
# FORCE=1 skips the soak and proof-run guard of test and deploy.
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1
MODE=${1:-}
NEW=skn/openplc:log077
OLD=skn/openplc:pre-log077
LOCAL=skn/openplc:v0.1
REG=localhost:5000/skn/openplc:v0.1
CT=openplc-log077-test
WANT="200 302 plant_trips 000"
T=""
POD=""
step() { echo; echo "== $(date +%H:%M:%S) $*"; }
pass() { echo "PASS $1"; }
die() { echo "STOP $*"; exit 1; }

# The probe runs inside an OpenPLC container. It reads the form body "username=openplc&password=<value>"
# on stdin and prints four words: <vendor default login> <Secret login> <program> <port 8443>.
# 200 = login refused, 302 = login taken, 000 = nothing answers. A LOG-077 image prints "200 302 plant_trips 000".
PROBE='d=$(curl -s -o /dev/null -m 8 -w "%{http_code}" -d "username=openplc&password=openplc" http://127.0.0.1:8080/login)
s=$(curl -s -c /tmp/probe.jar -o /dev/null -m 8 -w "%{http_code}" --data-binary @- http://127.0.0.1:8080/login)
p=-
if [ "$s" = 302 ]; then
  p=other
  curl -s -b /tmp/probe.jar -m 8 -o /tmp/probe.html http://127.0.0.1:8080/dashboard
  grep -q "Program:</b> plant_trips</p>" /tmp/probe.html && grep -q "Running</font>" /tmp/probe.html && p=plant_trips
fi
rm -f /tmp/probe.jar /tmp/probe.html
r=$(curl -sk -o /dev/null -m 5 -w "%{http_code}" https://127.0.0.1:8443/api/ping)
echo "$d $s $p $r"'

# probe_pod <deploy/openplc | pod/NAME>: the probe with the password from the Secret. The password moves
# through pipes only, never through a command line.
probe_pod() {
  { printf 'username=openplc&password='
    kubectl -n plant get secret openplc-auth -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null
  } | kubectl -n plant exec -i "$1" -- sh -c "$PROBE" 2>/dev/null
}
pyget() {   # pyget <ns> <deploy> <url> <python expression over d>: the same probe as in golive.sh
  kubectl -n "$1" exec "deploy/$2" -- python -c "import json,urllib.request as u
d=json.load(u.urlopen('$3', timeout=5))
print('OK' if ($4) else 'NO')" 2>/dev/null | grep -qx OK
}
wait_for() {   # wait_for <seconds> <command...>: true as soon as the command passes
  local secs=$1
  shift
  for _ in $(seq 1 $((secs / 5))); do
    "$@" && return 0
    sleep 5
  done
  return 1
}
closed_loop() { pyget plant plant-sim http://127.0.0.1:9200/state "d['plc']['mode'] == 'closed-loop'"; }
plant_quiet() {
  pyget plant plant-sim http://127.0.0.1:9200/state \
    "not d['active_faults'] and not any(v['tripped'] for v in d['devices'].values())"
}
one_pod() { [ "$(kubectl -n plant get pod -l app=openplc -o name 2>/dev/null | wc -l)" = 1 ]; }
running_ref() {   # the image of the running openplc container, as <repository>@sha256:<digest>
  kubectl -n plant get pod -l app=openplc --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].status.containerStatuses[0].imageID}' 2>/dev/null | sed 's|^[a-z-]*://||'
}
guard() {   # test loads the CPU and deploy restarts the PLC. Neither may run during a soak or a proof run.
  [ "${FORCE:-0}" = 1 ] && return 0
  local screens
  screens=$(screen -ls 2>/dev/null)
  if grep -qE '[0-9]+\.(visr-ps0|visr-proof)[[:space:]]' <<< "$screens"; then
    die "a soak (screen visr-ps0) or a proof run (screen visr-proof) is active. FORCE=1 skips this guard."
  fi
}

run_test() {
  guard
  step "test 1: build $NEW on top of the running image"
  local base got B
  base=$(running_ref)
  [ -n "$base" ] || base=$LOCAL
  docker image inspect "$base" >/dev/null 2>&1 || die "docker has no local copy of $base"
  echo "base image: $base"
  # Only the REST API edit and the two project files change. The OpenPLC runtime layers stay the same.
  B=$(mktemp -d)
  cp plc/rest-off.sh plc/program.st plc/entrypoint.sh "$B/" || die "plc/ misses a file"
  cat > "$B/Dockerfile" <<EOF
FROM $base
COPY rest-off.sh /rest-off.sh
RUN sh /rest-off.sh
COPY program.st /program.st
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EOF
  docker build -t "$NEW" "$B" || { rm -rf "$B"; die "the build of $NEW failed"; }
  rm -rf "$B"
  pass "built $NEW"

  step "test 2: run $NEW in a throwaway container (no network, throwaway password)"
  T=$(mktemp -d)
  trap 'docker rm -f "$CT" >/dev/null 2>&1; rm -rf "$T"' EXIT
  ( umask 077; openssl rand -hex 24 | tr -d '\n' > "$T/password" )
  docker rm -f "$CT" >/dev/null 2>&1
  docker run -d --name "$CT" --network none -v "$T/password:/etc/openplc-auth/password:ro" "$NEW" >/dev/null \
    || die "the test container did not start"
  if ! wait_for 240 sh -c "docker logs $CT 2>&1 | grep -q 'bring-up finished'"; then
    docker logs "$CT" 2>&1 | tail -40
    die "the entrypoint in the test container did not finish in 240 s"
  fi
  docker logs "$CT" 2>&1 | grep '^\[entrypoint\]'
  got=$({ printf 'username=openplc&password='; cat "$T/password"; } | docker exec -i "$CT" sh -c "$PROBE")
  echo "probe: ${got:-no answer} (want: $WANT)"
  [ "$got" = "$WANT" ] || die "the test container does not give the wanted probe"
  pass "the new image refuses the vendor default, takes the Secret password, runs plant_trips, and keeps port 8443 closed"
  docker rm -f "$CT" >/dev/null 2>&1
  rm -rf "$T"
  trap - EXIT
}

verify_new() {   # true when the new pod runs plant_trips and the trip loop is closed-loop
  local log
  kubectl -n plant rollout status deploy/openplc --timeout=240s >/dev/null \
    || { echo "FAIL the rollout did not finish in 240 s"; return 1; }
  wait_for 90 one_pod || { echo "FAIL the old openplc pod did not stop in 90 s"; return 1; }
  POD=$(kubectl -n plant get pod -l app=openplc -o jsonpath='{.items[0].metadata.name}')
  if ! wait_for 180 sh -c "kubectl -n plant logs $POD 2>/dev/null | grep -q 'bring-up finished'"; then
    kubectl -n plant logs "$POD" 2>&1 | tail -30
    echo "FAIL the entrypoint of $POD did not finish in 180 s"
    return 1
  fi
  log=$(kubectl -n plant logs "$POD" 2>/dev/null)
  grep '^\[entrypoint\]' <<< "$log"
  grep -q '^\[entrypoint\] PASS the runtime runs plant_trips' <<< "$log" \
    || { echo "FAIL the entrypoint of $POD does not report plant_trips running"; return 1; }
  wait_for 120 closed_loop || { echo "FAIL the trip loop is not closed-loop 120 s after the bring-up"; return 1; }
  pass "the new pod $POD runs plant_trips, and the trip loop is closed-loop"
}

run_rollback() {   # true when the old image closes the trip loop again
  step "rollback: push $OLD as $REG, then restart deploy/openplc"
  docker image inspect "$OLD" >/dev/null 2>&1 || { echo "FAIL there is no $OLD image to roll back to"; return 1; }
  { docker tag "$OLD" "$REG" && docker push -q "$REG" >/dev/null; } || { echo "FAIL the push of $OLD failed"; return 1; }
  docker tag "$OLD" "$LOCAL"
  kubectl -n plant rollout restart deploy/openplc >/dev/null
  kubectl -n plant rollout status deploy/openplc --timeout=240s >/dev/null || echo "WARN the rollout did not finish in 240 s"
  wait_for 90 one_pod || echo "WARN the old openplc pod did not stop in 90 s"
  sleep 10   # the sim sees the lost connection first, so the next closed-loop answer comes from the new pod
  if wait_for 240 closed_loop; then
    pass "the trip loop is closed-loop on $OLD"
    return 0
  fi
  echo "FAIL the trip loop is not closed-loop 240 s after the rollback"
  return 1
}
rollback_or_die() {
  if run_rollback; then die "the rollout failed. The image from before LOG-077 runs again."; fi
  die "the rollout failed, and the rollback did not close the trip loop. Read: kubectl -n plant logs deploy/openplc"
}

run_deploy() {
  local nodes ref gen got bad=0
  step "deploy 0: preflight"
  guard
  nodes=$(kubectl get nodes 2>/dev/null)
  grep -q " Ready" <<< "$nodes" || die "k3s is not Ready"
  curl -sf -m 5 http://127.0.0.1:5000/v2/_catalog >/dev/null || die "the registry on 127.0.0.1:5000 does not answer"
  plant_quiet || die "a fault is active or a machine has a latched trip. Reset the plant, let it cool, then run again."
  if closed_loop; then pass "the trip loop is closed-loop before the rollout"
  else echo "WARN the trip loop is not closed-loop before the rollout"; fi

  run_test

  step "deploy 1: keep the running image as $OLD (the rollback copy)"
  ref=$(running_ref)
  if docker image inspect "$OLD" >/dev/null 2>&1; then
    echo "$OLD exists from an earlier run and stays"
  elif [ -z "$ref" ]; then
    die "no openplc pod runs, so there is no image to keep"
  elif [ "$(docker run --rm --network none --entrypoint grep "$ref" -c 'target=run_https' /OpenPLC_v3/webserver/webserver.py)" = 1 ]; then
    docker tag "$ref" "$OLD" || die "the tag of $ref as $OLD failed"
    pass "kept $ref as $OLD"
  else
    echo "the running image has the REST API off already, so it is not a rollback copy. No $OLD tag."
  fi

  step "deploy 2: Secret, push, apply, restart"
  bash deploy/openplc-auth.sh || die "deploy/openplc-auth.sh failed. Nothing else changed."
  { docker tag "$NEW" "$REG" && docker push -q "$REG" >/dev/null; } \
    || die "the push of $REG failed. The running pod did not change."
  docker tag "$NEW" "$LOCAL"
  pass "pushed $NEW as $REG"
  gen=$(kubectl -n plant get deploy openplc -o jsonpath='{.metadata.generation}')
  kubectl apply -f deploy/openplc.yaml || { echo "FAIL kubectl apply -f deploy/openplc.yaml failed"; rollback_or_die; }
  # A changed pod template starts a rollout by itself. Else a restart makes the pod pull the new push.
  if [ "$(kubectl -n plant get deploy openplc -o jsonpath='{.metadata.generation}')" = "$gen" ]; then
    kubectl -n plant rollout restart deploy/openplc >/dev/null
  fi

  step "deploy 3: verify the new pod"
  verify_new || { echo "FAIL the new pod did not close the trip loop with plant_trips"; rollback_or_die; }
  got=$(probe_pod "pod/$POD")
  echo "probe: ${got:-no answer} (want: $WANT)"
  set -- $got
  if [ "${1:-}" = 200 ]; then pass "the web UI refuses the vendor default login"
  else echo "FAIL the vendor default login answers ${1:-nothing}, not 200 (refused)"; bad=1; fi
  if [ "${2:-}" = 302 ]; then pass "the password from Secret plant/openplc-auth logs in"
  else echo "FAIL the Secret password login answers ${2:-nothing}, not 302 (taken)"; bad=1; fi
  if [ "${4:-}" = 000 ]; then pass "nothing answers on the REST API port 8443"
  else echo "FAIL the REST API port 8443 answers ${4:-nothing}"; bad=1; fi
  echo
  if [ "$bad" = 1 ]; then
    echo "== DONE $(date +%H:%M:%S) with FAIL lines. The trip loop works, so the new image stays. Read the pod log."
    exit 1
  fi
  echo "== DONE $(date +%H:%M:%S). The LOG-077 image runs. Web UI: http://<box>:30081, user openplc."
  echo "   Read the password: kubectl -n plant get secret openplc-auth -o jsonpath='{.data.password}' | base64 -d; echo"
}

case "$MODE" in
  test)
    run_test
    echo
    echo "== DONE $(date +%H:%M:%S). The test passed. k3s and the registry did not change." ;;
  deploy)
    run_deploy ;;
  rollback)
    run_rollback || exit 1
    echo
    echo "== DONE $(date +%H:%M:%S). The image from before LOG-077 runs. Its web login is the vendor default again." ;;
  probe)   # exit 0 only for the wanted four words
    got=$(probe_pod deploy/openplc)
    echo "$got"
    [ "$got" = "$WANT" ] ;;
  *)
    echo "usage: bash deploy/openplc-rollout.sh test|deploy|rollback|probe"
    exit 2 ;;
esac
