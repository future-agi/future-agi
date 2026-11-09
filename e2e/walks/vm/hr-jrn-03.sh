#!/usr/bin/env bash
# Cloud-only HR-JRN-03 proof. Raw logs stay on VM; out contains value-free gates.
set -uo pipefail
[[ $(uname -s) == Linux ]] || exit 1
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
MODE=${1:?gate, walk or all}; EXPECT=${2:?exact head}
cd "$ROOT"
mkdir -p out
# Ruling 724: every command, including shell/infra failures, lands its real code.
finish() { rc=$?; trap - EXIT; echo "$rc" > "$ROOT/out/command-exit.txt"; ((rc == 0)) && exit 0 || exit 1; }
trap finish EXIT
[[ $(git rev-parse HEAD) == "$EXPECT" ]] || exit 71
[[ -z $(git status --porcelain --untracked-files=no) ]] || exit 72
export CI=1 PLAYWRIGHT_BROWSERS_PATH=$HOME/ms-pw
export FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 FUTURE_AGI_TELEMETRY_URL=http://127.0.0.1:9 FUTURE_AGI_TELEMETRY_DISABLED=true
export OLLAMA_HOST=127.0.0.1:9 OLLAMA_URL=http://127.0.0.1:9 OLLAMA_BASE_URL=http://127.0.0.1:9
export LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONDONTWRITEBYTECODE=1
step() {
  local name=$1; shift
  local rc=0
  "$@" > "$ROOT/$name.log" 2>&1 || rc=$?
  echo "$name rc=$rc" | tee -a "$ROOT/out/steps.txt"
  return "$rc"
}
if [[ "$MODE" == gate || "$MODE" == all ]]; then
  cd e2e
  failed=0
  step typecheck yarn typecheck || failed=1
  step catalog yarn catalog || failed=1
  step catalog-check yarn catalog:check || failed=1
  step mock-counter node --test scripts/mock-counts.test.mjs scripts/mock-llm.test.mjs || failed=1
  step reader env E2E_WALK_COUNTS="$ROOT/out/reader-counts.json" yarn playwright test harness/census-result.spec.ts \
    --reporter=line,./walks/vm/counts-reporter.cjs --workers=2 || failed=1
  step regressions env E2E_WALK_COUNTS="$ROOT/out/regression-counts.json" yarn playwright test harness/walk-regressions.spec.ts harness/gateway-admin.spec.ts \
    --grep offline --reporter=line,./walks/vm/counts-reporter.cjs --workers=2 || failed=1
  red=0
  step fallibility env E2E_CENSUS_PERTURB=1 E2E_WALK_COUNTS="$ROOT/out/red-counts.json" yarn playwright test harness/census-result.spec.ts \
    --grep 'fallibility:' --reporter=line,./walks/vm/counts-reporter.cjs --workers=1 || red=$?
  # Quote only the expected synthetic failure, not raw logs or fixture values.
  grep -F 'CAT2_OFFERED: api: google/lyria-3-pro-preview' "$ROOT/fallibility.log" | head -1 > "$ROOT/out/fallibility.txt"
  [[ "$red" == 1 && -s "$ROOT/out/fallibility.txt" ]] || failed=1
  step restored env E2E_WALK_COUNTS="$ROOT/out/restored-counts.json" yarn playwright test harness/census-result.spec.ts \
    --reporter=line,./walks/vm/counts-reporter.cjs --workers=2 || failed=1
  # Type diagnostics are source locations/codes only; unknown errors stay local.
  node - "$ROOT" <<'NODE'
const fs = require('node:fs'); const root = process.argv[2];
const diagnostics = fs.readFileSync(root + '/typecheck.log', 'utf8').split('\n').flatMap(line => {
  const m = /^([A-Za-z0-9_./-]+)\((\d+),(\d+)\): error (TS\d+):/.exec(line);
  return m ? [{ file: m[1], line: +m[2], column: +m[3], code: m[4] }] : [];
});
fs.writeFileSync(root + '/out/diagnostics.json', JSON.stringify(diagnostics));
NODE
  ((failed == 0)) || exit "$failed"
  [[ "$MODE" == gate ]] && exit 0
  cd "$ROOT"
fi
[[ "$MODE" == walk || "$MODE" == all ]] || exit 2
export E2E_STACK=standalone E2E_GATEWAY_PROFILE=stock E2E_WALK_PINS=1 E2E_GATEWAY_REPLICAS=1
export E2E_WALK_PRODUCT_SHA="$EXPECT" E2E_WALK_STRICT_BROWSER=1 E2E_WALK_WORKERS=2
export E2E_CENSUS_RUN_ID="hr03-$EXPECT-$(date +%s)-$$" E2E_CENSUS_RESULTS="$ROOT/out/census-results"
export E2E_WALK_OUT="$ROOT/walk-artifacts" E2E_WALK_IMAGES="$ROOT/images.json"
[[ -z "${DOCKER_HOST:-}" ]] && export DOCKER_CONTEXT=$(docker context show)
step build bin/e2e build standalone || exit 3
step image-build node e2e/scripts/walk-images.mjs built standalone "$EXPECT" "$ROOT/built-images.json" || exit 3
step up timeout 1500 bin/e2e up || exit 3
node e2e/scripts/walk-images.mjs running standalone "$EXPECT" "$ROOT/built-images.json" > "$E2E_WALK_IMAGES" || exit 3
# Live negative control first. A mock API input offers one prohibited identity;
# the other catalogue and expected contract are untouched. No green artifact
# can publish from this deliberately dirty run. Restore BEFORE acceptance.
node e2e/walks/vm/census-fallibility.mjs "$ROOT" perturb || exit 1
cd e2e
red=0
step census-red env E2E_WALK_OUT="$ROOT/red-artifacts" E2E_CENSUS_RESULTS="$ROOT/out/red-results" \
  E2E_CENSUS_RUN_ID="$E2E_CENSUS_RUN_ID-red" E2E_WALK_COUNTS="$ROOT/out/live-red-counts.json" yarn playwright test --config playwright.census.config.ts \
  --project chromium --grep '@census-consumer' --reporter=line,./walks/vm/counts-reporter.cjs || red=$?
cd "$ROOT"
node e2e/walks/vm/census-fallibility.mjs "$ROOT" restore || exit 1
rc=0
grep -F 'CAT2_OFFERED: api: google/lyria-3-pro-preview' "$ROOT/census-red.log" | head -1 > out/live-fallibility.txt
[[ "$red" == 1 && -s out/live-fallibility.txt ]] || rc=1
[[ -z $(git status --porcelain --untracked-files=no) ]] || exit 72
cd e2e
step census env E2E_WALK_COUNTS="$ROOT/out/walk-counts.json" yarn playwright test --config playwright.census.config.ts \
  --project chromium --project firefox --grep '@census-consumer' --reporter=line,./walks/vm/counts-reporter.cjs || rc=$?
cd "$ROOT"
step evidence node e2e/walks/vm/census-evidence.mjs "$ROOT" || rc=1
step down bin/e2e down || rc=1
exit "$rc"
