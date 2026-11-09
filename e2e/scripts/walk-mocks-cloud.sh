#!/usr/bin/env bash
# One STANDARD VM; ordinary unit/mock gates, never an egress-kit product walk.
set -euo pipefail
ROOT=$(pwd)
OUT=${1:?absolute evidence directory required}
mkdir -p "$OUT"
# Preserve the command's real status without leaking rig-reserved exit codes.
finish() {
  local rc=$?
  if declare -F cleanup >/dev/null; then cleanup; fi
  printf '%s\n' "$rc" >"$OUT/command-exit.txt"
  [ "$rc" -eq 0 ] && exit 0 || exit 1
}
trap finish EXIT
export CI=1 PYTHONDONTWRITEBYTECODE=1
export OLLAMA_HOST=127.0.0.1:9 OLLAMA_URL=http://127.0.0.1:9 OLLAMA_BASE_URL=http://127.0.0.1:9
export FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 LITELLM_LOCAL_MODEL_COST_MAP=True
export GOMAXPROCS=$(nproc)
[ "$(git rev-parse HEAD)" = "${OMEGA_EXEC_SOURCE_HEAD:?exact source head required}" ]
[ -z "$(git status --porcelain --untracked-files=no)" ]
printf 'head=%s\nproductAccepted=false\n' "$(git rev-parse HEAD)" >"$OUT/head.txt"
(cd agentcc-gateway && go version && go version | grep -E 'go1\.26\.' && go test -p "$(nproc)" -race -count=1 ./cmd/agentcc-mcpmock/... ./cmd/agentcc-otlpmock/...) 2>&1 | tee "$OUT/go.log"
node --test e2e/scripts/mock-llm.test.mjs e2e/scripts/walk-mocks.test.mjs 2>&1 | tee "$OUT/node.log"
(cd e2e && node_modules/.bin/tsc --noEmit) 2>&1 | tee "$OUT/types.log"
(cd e2e && node_modules/.bin/playwright test harness/managed-mock.spec.ts harness/walk-mock-contract.spec.ts --grep 'managed mock|walk mock offline' --workers "$(nproc)" --reporter=list) 2>&1 | tee "$OUT/contract.log"
# Share the same per-project runtime key cache as bin/e2e.
. "$ROOT/bin/lib/e2e-runtime-credentials.sh"
prepare_runtime_credentials walk-mocks
# Resolve both production-topology overlays with fixture env files only. This
# stage starts their MOCK service slice, not the real product or its datastores.
for shape in standalone distributed; do
  if [ "$shape" = standalone ]; then BASE=docker-compose.yml; ENV=e2e/stack/standalone-e2e.env; OVERLAY=e2e/stack/docker-compose.standalone-e2e.yml;
  else BASE=docker-compose.distributed.yml; ENV=e2e/stack/e2e.env; OVERLAY=e2e/stack/docker-compose.e2e.yml; fi
  # Do not copy runtime encryption keys into evidence artifacts.
  docker compose --project-directory "$ROOT" --env-file "$ENV" -f "$BASE" -f "$OVERLAY" -p walk-mocks config --format json | python3 -I -c '
import json, sys
document = json.load(sys.stdin)
for service in document.get("services", {}).values():
    environment = service.get("environment") or {}
    for name in ("INTEGRATION_ENCRYPTION_KEY", "WEBHOOKS_ENCRYPTION_KEYS"):
        environment.pop(name, None)
json.dump(document, sys.stdout, indent=2)
' >"$OUT/compose-$shape.json"
done
COMPOSE=(docker compose --project-directory "$ROOT" --env-file e2e/stack/standalone-e2e.env -f docker-compose.yml -f e2e/stack/docker-compose.standalone-e2e.yml -p walk-mocks)
cleanup() {
  "${COMPOSE[@]}" logs --no-color >"$OUT/stack.log" 2>&1 || true
  "${COMPOSE[@]}" down >"$OUT/down.log" 2>&1 || true
}
"${COMPOSE[@]}" up --build -d --wait --wait-timeout 300 mock-llm 2>&1 | tee "$OUT/stack-up.log"
"${COMPOSE[@]}" cp mock-certs:/certs/cert.pem "$OUT/cert.pem"
export WALK_MOCK_CA="$OUT/cert.pem"
for port in 28082 28084 28085 28086; do
  ready=0
  for _ in $(seq 1 30); do if curl -fsS "http://127.0.0.1:$port/__health" >/dev/null; then ready=1; break; fi; sleep 1; done
  [ "$ready" = 1 ]
done
node e2e/scripts/walk-mocks-smoke.mjs 2>&1 | tee "$OUT/smoke.log"
# The accepted runner already supplies browser OS dependencies. Do not attempt
# privileged apt installation from the unprivileged cloud job. The runner's
# baked browser store (/opt/ms-playwright) is root-owned and pinned to the
# runner's Playwright, so this tree's Playwright installs its own browser
# revisions into a writable per-user cache that install and test both use.
export PLAYWRIGHT_BROWSERS_PATH="$HOME/cache/ms-playwright"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
(cd e2e && node_modules/.bin/playwright install chromium firefox) 2>&1 | tee "$OUT/browser-install.log"
(cd e2e && E2E_WALK_MOCKS=1 node_modules/.bin/playwright test --config walk-mocks.config.ts --workers "$(nproc)") 2>&1 | tee "$OUT/browser.log"
[ -z "$(git status --porcelain --untracked-files=no)" ]
printf 'PASS unit, contracts, stack endpoint smoke, Chromium and Firefox consent and API boundaries\n' | tee "$OUT/result.txt"
