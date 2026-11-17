#!/usr/bin/env bash
# H14: the hosted-shape runner for W19 (cloud VM only).
# Usage: run-hosted.sh <group> <expected HEAD>
#   1. HEAD and a clean tree; refused (exit 3) when this head has no
#      deploy/docker-compose.hosted.yml (gw/G18-HOSTED-PREP-1 937892d353 has it);
#   2. build the converged backend, gateway and frontend images from this
#      checkout and push them to a registry container on the VM's loopback
#      (127.0.0.1:5000), so the hosted file's image@digest references resolve with
#      no registry or host outside the VM;
#   3. render the synthetic env (hosted-env.mjs) into a 0600 file outside the
#      checkout; start the stand-in datastores and the mock (docker-compose.hosted-walk.yml);
#   4. the phased migration job (`migrate`, profile walk-migrate) while a readiness
#      poller records backend /readyz/ and gateway /readyz every 5 s: not ready
#      until the migration finished is the expectation W19 asserts;
#   5. backend, both gateway replicas and the frontend; then the W19 walk
#      (playwright.walk.config.ts --grep the group's tag) against the loopback ports;
#   6. the key-transition rehearsal driver, when this head has one
#      (deploy/tests/hosted_migration_rehearsal.py, G18_SYNTHETIC_REHEARSAL=1,
#      loopback databases only), and the walk again after it;
#   7. down -v, the registry removed, the env file deleted.
# Results: e2e/walk-results/<group>/ (receipts and logs only; never the env file).
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'cloud VM only'; exit 70; }
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
GROUP=${1:?usage: run-hosted.sh <group> <expected HEAD>}
EXPECT=${2:?usage: run-hosted.sh <group> <expected HEAD>}
OUT=$ROOT/e2e/walk-results/$GROUP
HERE=$ROOT/e2e/walks/hosted
HOSTED=$ROOT/deploy/docker-compose.hosted.yml
mkdir -p "$OUT"
cd "$ROOT"
[[ $(git rev-parse HEAD) == "$EXPECT" ]] || { echo "HEAD is not $EXPECT"; exit 71; }
[[ -z $(git --no-optional-locks status --porcelain --untracked-files=no) ]] || { git status --short | head; exit 72; }
if [[ ! -f "$HOSTED" ]]; then
  echo "REFUSED: deploy/docker-compose.hosted.yml is not on this head (gw/G18-HOSTED-PREP-1 937892d353 has it)" | tee "$OUT/refused.txt"
  exit 3
fi
GREP=$(node -e 'console.log(JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))[process.argv[2]].grep)' \
  "$ROOT/e2e/walks/launch/groups.json" "$GROUP")
export CI=1 PLAYWRIGHT_BROWSERS_PATH=$HOME/ms-pw
WORK=$(mktemp -d /tmp/hosted-walk.XXXXXX); chmod 700 "$WORK"
ENVF=$WORK/hosted.env
REG=127.0.0.1:5000
P=(docker compose -p futureagi-e2e-hosted --env-file "$ENVF" -f "$HOSTED" -f "$HERE/docker-compose.hosted-walk.yml")
cleanup() {
  "${P[@]}" --profile walk-migrate down -v > "$OUT/down.log" 2>&1
  docker rm -f walk-registry > /dev/null 2>&1
  rm -rf "$WORK"
}
trap cleanup EXIT
note() { printf '%s\n' "$1" | tee -a "$OUT/steps.txt"; }

start=$SECONDS
docker run -d --name walk-registry -p "$REG:5000" registry:2 > /dev/null || { note "registry failed"; exit 1; }
build() { docker build -t "$REG/$1:walk" "${@:2}" >> "$OUT/build.log" 2>&1 && docker push "$REG/$1:walk" >> "$OUT/build.log" 2>&1; }
build futureagi-backend -f futureagi/Dockerfile.oss futureagi || { note "backend build failed"; exit 1; }
build agentcc-gateway agentcc-gateway || { note "gateway build failed"; exit 1; }
build futureagi-frontend frontend || { note "frontend build failed"; exit 1; }
digest() { docker inspect --format '{{index .RepoDigests 0}}' "$REG/$1:walk" | sed 's/.*@//'; }
images=$(printf '{"BACKEND_IMAGE":"%s","BACKEND_DIGEST":"%s","GATEWAY_IMAGE":"%s","GATEWAY_DIGEST":"%s","FRONTEND_IMAGE":"%s"}' \
  "$REG/futureagi-backend" "$(digest futureagi-backend)" "$REG/agentcc-gateway" "$(digest agentcc-gateway)" "$REG/futureagi-frontend:walk")
note "images built and pushed secs=$((SECONDS - start)) backend=sha256:$(digest futureagi-backend | sed 's/sha256://')"
node "$HERE/hosted-env.mjs" "$HOSTED" "$ENVF" "$images" > "$OUT/env.log" 2>&1 || { note "env render failed"; exit 1; }
"${P[@]}" up -d --wait postgres redis clickhouse temporal mock-llm > "$OUT/datastores.log" 2>&1 || { note "datastores failed"; exit 1; }

poll() { while :; do
  printf '%s backend=%s gateway=%s\n' "$(date -u +%T)" \
    "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8100/readyz/)" \
    "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:28090/readyz)"; sleep 5; done; }
"${P[@]}" up -d backend agentcc-gateway frontend > "$OUT/app-up.log" 2>&1
poll > "$OUT/readiness.tsv" & POLLER=$!
start=$SECONDS; mrc=0
"${P[@]}" --profile walk-migrate run --rm migrate > "$OUT/migrate.log" 2>&1 || mrc=$?
note "migrate schema rc=$mrc secs=$((SECONDS - start))"
for _ in $(seq 1 120); do
  [[ $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8100/readyz/) == 200 ]] && break; sleep 5
done
kill "$POLLER" 2>/dev/null
((mrc == 0)) || exit 1

# Registry is no longer needed after compose has pulled the local digests. It
# must not remain outside the selected networks at strict-kit admission.
docker rm -f walk-registry >/dev/null 2>&1
walk() {
  local phase=$1
  local cmd=(yarn playwright test --config playwright.walk.config.ts --grep "$GREP" --reporter=line)
  if [[ "${E2E_WALK_STRICT:-}" == 1 ]]; then
    local nets
    read -r -a nets <<<"$(docker network ls --filter label=com.docker.compose.project=futureagi-e2e-hosted --format '{{.Name}}' | tr '\n' ' ')"
    (cd e2e && E2E_WALK_OUT="walk-results/$GROUP/$phase" bash walks/kit/strict-kit.sh "${E2E_KIT_ARCHIVE:?}" \
      "$OUT/kit-$phase" "${nets[@]}" -- "${cmd[@]}") > "$OUT/walk-$phase.log" 2>&1
  else
    (cd e2e && E2E_WALK_OUT="walk-results/$GROUP/$phase" "${cmd[@]}") > "$OUT/walk-$phase.log" 2>&1
  fi
}
wrc=0; walk before-transition || wrc=$?
note "walk before-transition rc=$wrc"
if [[ -f deploy/tests/hosted_migration_rehearsal.py ]]; then
  rrc=0
  (cd futureagi && G18_SYNTHETIC_REHEARSAL=1 PG_HOST=127.0.0.1 .venv/bin/python ../deploy/tests/hosted_migration_rehearsal.py) \
    > "$OUT/rehearsal.log" 2>&1 || rrc=$?
  note "key-transition rehearsal rc=$rrc (loopback databases only)"
  walk after-transition || wrc=$?
  note "walk after-transition rc=$wrc"
else
  note "key-transition rehearsal: no driver on this head"
fi
exit "$wrc"
