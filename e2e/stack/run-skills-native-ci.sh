#!/usr/bin/env bash
# Run on a dedicated Linux CI worker with the source-built standalone images,
# e2e harness, Chromium, uv and the independently receipted Skills pair.
set -euo pipefail
[[ $# == 2 && $(uname -s) == Linux ]] || { echo 'Usage (Linux): run-skills-native-ci.sh <pair-dir> <new-venv-dir>' >&2; exit 2; }
dist=$(realpath "$1")
envdir=$(realpath -m "$2")
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
export E2E_STACK=standalone E2E_STANDALONE_NETWORK=native
E2E_GOOGLE_IMAGE_TAG=$(sed -n 's/^FUTURE_AGI_VERSION=//p' e2e/stack/standalone-e2e.env)
export E2E_GOOGLE_IMAGE_TAG=${E2E_GOOGLE_IMAGE_TAG:?Missing standalone backend image tag}
# J2_PREPARE_OUT would return early with the benchmark seed instead of running 022.
unset J2_PREPARE_OUT J2_DRIVER_FIXTURE_OUTPUT J2_EVIDENCE_DIR
runtime=e2e/stack/native-runtime
python3 -I "$runtime/generate_ci_dataset.py" | cmp - "$runtime/ci_dataset.json"
e2e/stack/prepare-skill-traces.sh interpreters "$dist" "$envdir"
export SKILL_TRACES_PYTHON="$envdir/traced/bin/python"
python3 -I e2e/stack/verify-skill-packages.py "$dist" "${SKILL_TRACE_PAIR_SHA256:?}" \
  "${CTXHUB_APP_COMMIT:?}" "${SDK_COMMIT:?}" "${TRACEAI_COMMIT:?}" --stage e2e/stack >/dev/null
bin/e2e compose build native-runtime
bin/e2e up
J2_RUNTIME_CONTAINER=$(bin/e2e compose ps -q native-runtime)
export J2_RUNTIME_CONTAINER
[[ -n "$J2_RUNTIME_CONTAINER" ]] || { echo 'Native runtime did not start' >&2; exit 1; }
bin/e2e compose exec -T native-runtime cat /opt/ctxhub/ci_dataset.json | cmp - "$runtime/ci_dataset.json"

# Allow the caller to retain a local JSON report for the common coverage gate.
# Do not publish this raw report: the browser attachments belong to this worker.
PLAYWRIGHT_JSON_OUTPUT_FILE=$(realpath -m "${PLAYWRIGHT_JSON_OUTPUT_FILE:-$envdir/native-playwright.json}")
export PLAYWRIGHT_JSON_OUTPUT_FILE
rc=0
bin/e2e test flows/skills/j2-captured-source.spec.ts flows/skills/j2-native-verification.spec.ts flows/skills/skill-traces.spec.ts \
  --grep 'SKILLS-E2E-(021|022|108):' --workers=1 --retries=0 --forbid-only --reporter=json --trace off >"$envdir/native-browser.log" 2>&1 || rc=$?
if (( rc != 0 )); then echo "Native browser failed (exit $rc)" >&2; exit "$rc"; fi
# Print only case IDs/statuses; a missing, skipped, expected-failure or retry-only
# result is not evidence for a journey, even if Playwright itself returned 0.
jq -e '
  [.suites[] | recurse(.suites[]?) | .specs[]? | .title as $title | .tests[] |
    {id: ($title | split(":")[0]), expected: .expectedStatus, attempts: [.results[] | .status]}] |
  sort_by(.id) | ., (
    map(.id) == ["SKILLS-E2E-021", "SKILLS-E2E-022", "SKILLS-E2E-108"] and
    all(.[]; .expected == "passed" and .attempts == ["passed"])
  )' "$PLAYWRIGHT_JSON_OUTPUT_FILE"
exit "$rc"
