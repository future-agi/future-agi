#!/bin/sh
# The checker image's entrypoint in every shipped deploy shape (deploy/README.md, "Omega Hub"; the
# Helm chart's `hub` section). Before the worker starts it checks the runner daemon, the Docker the
# checker runs uploads on, and refuses to start on one that would run them unsandboxed:
#
#   1. DOCKER_HOST names the runner daemon: the dedicated runner host in production, the hub-runner
#      service (deploy/hub-runner) in the Compose setups. Unset, the docker CLI would fall back to a
#      local socket, so the checker refuses to start.
#   2. That daemon's default runtime is OMEGA_HUB_RUNNER_RUNTIME (runsc unless set): the checker passes
#      no --runtime, so the default runtime is what runs every upload. runc is accepted only with
#      OMEGA_HUB_ALLOW_RUNC=1, which no shipped shape sets.
#   3. The runner image (OMEGA_HUB_RUNNER_IMAGE) is on that daemon; it is pulled there when it is not,
#      unless OMEGA_HUB_RUNNER_PULL=never. When it cannot be had, the checker exits 69, or with
#      OMEGA_HUB_RUNNER_WAIT=1 (the Compose evaluation setups) says why every minute and tries again,
#      so the rest of the stack still starts; uploads then wait in "received".
#   4. Under runsc, one container of the runner image, as the uploads' uid, really sees gVisor.
#   5. The judge preflight (src/preflight.mjs), under either runtime: one container started with exactly
#      an upload's docker flags proves on this daemon that the image's judge loads, that entry code
#      runs as uid 10001 with no capabilities, and that it is refused the judge's processes and files
#      (READINESS 3.19). A runner image whose judge would not hold refuses the checker's start.
#
# Then it execs the worker, `node src/main.mjs`, with this script's arguments. Exit codes: 64 a
# setting is missing or wrong, 65 the daemon would not sandbox uploads, 69 the daemon or the image
# cannot be reached.
set -eu

log() { printf 'omega-hub-checker: %s\n' "$*" >&2; }
fail() { code=$1; shift; log "$*"; exit "$code"; }

[ -n "${DOCKER_HOST:-}" ] || fail 64 "DOCKER_HOST is not set: set it to the runner daemon (tcp://<runner host>:2376 with DOCKER_TLS_VERIFY=1), never the application host's own Docker"
# A context would override nothing here (DOCKER_HOST wins), but the worker's own default names one.
DOCKER_CONTEXT=default
export DOCKER_CONTEXT

runtime=${OMEGA_HUB_RUNNER_RUNTIME:-runsc}
case "$runtime" in
  runsc) ;;
  runc) [ "${OMEGA_HUB_ALLOW_RUNC:-0}" = 1 ] || fail 64 "OMEGA_HUB_RUNNER_RUNTIME=runc runs uploads without gVisor; it needs OMEGA_HUB_ALLOW_RUNC=1 (development only)" ;;
  *) fail 64 "OMEGA_HUB_RUNNER_RUNTIME must be runsc or runc, not '$runtime'" ;;
esac
image=${OMEGA_HUB_RUNNER_IMAGE:-}
[ -n "$image" ] || fail 64 "OMEGA_HUB_RUNNER_IMAGE is not set: name the runner image (futureagi/omega-hub-runner:<version>, or a digest)"
[ -n "${OMEGA_HUB_CHECKER_TOKEN:-}" ] || fail 64 "OMEGA_HUB_CHECKER_TOKEN is not set: the checker and the backend must share it"

# 2. The daemon answers, and runs containers on the required runtime. `docker info --format` exits 0
#    with empty fields when no daemon answers, so the server's version is what proves it answers.
wait_s=${OMEGA_HUB_DAEMON_WAIT_SECONDS:-120}
waited=0
until server=$(docker version --format '{{.Server.Version}}' 2>&1) && [ -n "$server" ]; do
  [ "$waited" -lt "$wait_s" ] || fail 69 "the runner daemon at $DOCKER_HOST did not answer within ${wait_s}s: $(printf '%s' "$server" | tail -n 1)"
  sleep 2
  waited=$((waited + 2))
done
info=$(docker info --format '{{.DefaultRuntime}}|{{range $name, $_ := .Runtimes}}{{$name}} {{end}}' 2>&1) \
  || fail 69 "the runner daemon at $DOCKER_HOST did not describe itself: $(printf '%s' "$info" | tail -n 1)"
default=${info%%|*}
runtimes=${info#*|}
case " $runtimes " in
  *" $runtime "*) ;;
  *) fail 65 "the runner daemon at $DOCKER_HOST has no '$runtime' runtime (it has: ${runtimes% }); install gVisor there (deploy/hub-runner)" ;;
esac
[ "$default" = "$runtime" ] || fail 65 "the runner daemon at $DOCKER_HOST runs containers on '$default' by default, not '$runtime'; set \"default-runtime\": \"$runtime\" in its daemon.json (deploy/hub-runner/daemon.json)"
log "runner daemon $DOCKER_HOST: default runtime $default"

# 3. The runner image is on the runner daemon.
have_image() { docker image inspect "$image" >/dev/null 2>&1; }
until have_image; do
  if [ "${OMEGA_HUB_RUNNER_PULL:-missing}" != never ] && out=$(docker pull --quiet "$image" 2>&1); then
    log "pulled $image onto the runner daemon"
    break
  fi
  why="the runner image $image is not on the runner daemon at $DOCKER_HOST"
  [ "${OMEGA_HUB_RUNNER_PULL:-missing}" = never ] || why="$why and cannot be pulled ($(printf '%s' "${out:-}" | tail -n 1))"
  why="$why; build it with services/omega-hub-checker/runner/build.sh and load it there (docker save <image> | DOCKER_HOST=... docker load)"
  [ "${OMEGA_HUB_RUNNER_WAIT:-0}" = 1 ] || fail 69 "$why"
  log "$why; trying again in 60s (uploads wait in 'received' until then)"
  sleep 60
done

# 4. Under runsc, observe it: a runner-image container must see gVisor's kernel (its release ends in
#    -gvisor, or its log starts "Starting gVisor"), not the host's.
if [ "$runtime" = runsc ]; then
  seen=$(docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
    --entrypoint sh "$image" -c 'uname -r; dmesg 2>/dev/null | head -n 1' 2>&1) || fail 65 "a test container on the runner daemon at $DOCKER_HOST did not run: $(printf '%s' "$seen" | tail -n 1)"
  case "$seen" in
    *gvisor*|*"Starting gVisor"*) log "a runner container sees gVisor ($(printf '%s' "$seen" | head -n 1))" ;;
    *) fail 65 "a runner container on $DOCKER_HOST sees kernel '$(printf '%s' "$seen" | head -n 1)', not gVisor: the daemon's runsc is not gVisor" ;;
  esac
fi

# 5. The judge preflight: it prints why on failure and exits 65 (the judge would not hold) or 69 (the
#    daemon did not run it).
node src/preflight.mjs || { code=$?; exit "$code"; }

exec node src/main.mjs "$@"
