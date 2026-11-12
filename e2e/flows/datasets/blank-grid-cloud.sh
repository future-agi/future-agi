#!/usr/bin/env bash
# Execute on the disposable Linux fixture VM only.
set -euo pipefail
[[ $(uname -s) == Linux ]] || exit 1
umask 077
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
OUT="$ROOT/f38-proof-out"
mkdir -p "$OUT"
export CI=1 NO_COLOR=1 PLAYWRIGHT_BROWSERS_PATH="$HOME/ms-pw"
export FUTURE_AGI_VERSION=e2e-local FRONTEND_VERSION=e2e-local E2E_FI_COLLECTOR_VERSION=e2e-local
export E2E_COMPOSE_PROJECT=dsx-create-upload
export FUTURE_AGI_LICENSE_URL=http://localhost:9 FUTURE_AGI_TELEMETRY_DISABLED=true AWS_EC2_METADATA_DISABLED=true
COMPOSE=(bash bin/e2e compose -f "$ROOT/e2e/flows/datasets/create-upload-isolation.yml")
case "${1:-}" in
  check)
    (cd frontend && yarn install --frozen-lockfile --ignore-scripts --network-timeout 600000) > "$OUT/frontend-install.log" 2>&1
    (cd frontend && node node_modules/.bin/vitest run \
      src/sections/develop/AddDatasetDrawer/__tests__/BlankGridColumns.test.jsx \
      src/launch/__tests__/registry-705.test.js \
      src/launch/__tests__/registry-705-wiring.test.js \
      src/launch/__tests__/registry-705-claims.test.js \
      --pool=threads --maxWorkers=16 --minWorkers=16) > "$OUT/unit.log" 2>&1
    (cd e2e && yarn install --frozen-lockfile --ignore-scripts --network-timeout 600000 && yarn typecheck && yarn catalog) > "$OUT/catalog.log" 2>&1
    cp e2e/FLOWS.md "$OUT/FLOWS.md"
    tail -20 "$OUT/unit.log"; tail -10 "$OUT/catalog.log"
    ;;
  backend)
    git rev-parse HEAD > "$OUT/source-head.txt"
    bash bin/e2e build backend > "$OUT/backend-build.log" 2>&1
    bash e2e/flows/datasets/create-upload-cloud.sh up
    bash "$0" python-tests
    ;;
  python-tests)
    docker build -t futureagi/blank-grid-tests:local -f e2e/flows/datasets/blank-grid-tests/Dockerfile . > "$OUT/test-image.log" 2>&1
    "${COMPOSE[@]}" -f e2e/flows/datasets/blank-grid-tests.yml run --rm --no-deps --entrypoint python \
      -e DJANGO_SETTINGS_MODULE=tfc.settings.test \
      -e PG_HOST=postgres -e PG_PORT=5432 -e PG_DB=futureagi -e PG_USER=futureagi -e PG_PASSWORD=futureagi \
      -e REDIS_URL=redis://redis:6379/0 -e CH_HOST=clickhouse -e CH_PORT=9000 \
      backend -m pytest model_hub/tests/test_blank_grid.py -q > "$OUT/backend-tests.log" 2>&1
    cat "$OUT/backend-tests.log"
    ;;
  candidate|activate)
    # Test-build-only activation. Land the patch separately from the source head.
    node --input-type=module -e '
      import fs from "node:fs";
      const path = "frontend/src/launch/madeReal.js";
      let text = fs.readFileSync(path, "utf8");
      if (!text.includes("\"dataset.imports.blankGrid\"")) {
        const marker = "  IN: Object.freeze([";
        if (!text.includes(marker)) throw new Error("Missing IN slot");
        text = text.replace(marker, `${marker}\n    "dataset.imports.blankGrid", // DATA-E2E-007: candidate fixture walk.`);
        fs.writeFileSync(path, text);
      }
    '
    git diff -- frontend/src/launch/madeReal.js > "$OUT/fixture-activation.patch"
    if [[ "$1" == candidate ]]; then bash "$0" frontend; fi
    ;;
  frontend)
    bash bin/e2e build frontend > "$OUT/frontend-build.log" 2>&1
    bash e2e/flows/datasets/create-upload-cloud.sh up
    ;;
  scope)
    (cd e2e && yarn install --frozen-lockfile --ignore-scripts --network-timeout 600000) > "$OUT/e2e-install.log" 2>&1
    node e2e/flows/datasets/create-upload-ports.mjs bash bin/e2e test \
      flows/datasets/blank-grid-scope.spec.ts --browser=chromium --workers=1 --reporter=line > "$OUT/scope.log" 2>&1
    cat "$OUT/scope.log"
    ;;
  proof)
    browser=${2:?browser required}
    anchor=${3:-green}
    [[ "$browser" == chromium || "$browser" == firefox ]] || exit 1
    [[ "$anchor" == green || "$anchor" == schema || "$anchor" == rows || "$anchor" == isolation ]] || exit 1
    if [[ "$anchor" == green ]]; then unset E2E_BLANK_PERTURB; else export E2E_BLANK_PERTURB="$anchor"; fi
    label="$browser-$anchor"
    set +e
    PLAYWRIGHT_HTML_OUTPUT_DIR="$OUT/$label-report" PLAYWRIGHT_HTML_OPEN=never \
      node e2e/flows/datasets/create-upload-ports.mjs bash bin/e2e test \
      flows/datasets/blank-grid.spec.ts --browser="$browser" --workers=1 --reporter=line,html \
      --output="$OUT/$label-results" > "$OUT/$label.log" 2>&1
    code=$?
    set -e
    echo "$code" > "$OUT/$label.exit"
    tail -100 "$OUT/$label.log"
    [[ "$code" == 0 ]]
    ;;
  *) echo 'usage: blank-grid-cloud.sh check|backend|candidate|frontend|scope|proof chromium|firefox [anchor]'; exit 1 ;;
esac
