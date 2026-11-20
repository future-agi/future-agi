#!/usr/bin/env bash
# The ordinary suite is the exact complement on each of its original stacks.
set -euo pipefail
cohort=${1:?cohort name}
export SKILLS_COHORT="$cohort"
tmp=$(mktemp -d "${RUNNER_TEMP:?}/skills-results.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
coverage=e2e/scripts/skills-coverage.py
PLAYWRIGHT_JSON_OUTPUT_FILE="$tmp/list.json" bin/e2e test --list --reporter=json
pattern=$(python3 -I "$coverage" pattern "$tmp/list.json" "$cohort")
if [[ $cohort == ordinary ]]; then
  bin/e2e test --grep "$pattern"
else
  # Keep raw diagnostics and private attachments on this ephemeral machine.
  rc=0
  export PLAYWRIGHT_JSON_OUTPUT_FILE="$tmp/results.json"
  if [[ $cohort == native ]]; then
    python3 -I e2e/stack/verify-skill-packages.py "$RUNNER_TEMP/skill-pair" "$SKILL_TRACE_PAIR_SHA256" \
      "$CTXHUB_APP_COMMIT" "$SDK_COMMIT" "$TRACEAI_COMMIT" --require-sums >/dev/null
    e2e/stack/run-skills-native-ci.sh "$RUNNER_TEMP/skill-pair" "$RUNNER_TEMP/native-python" || rc=$?
  else
    if [[ $cohort == host || $cohort == campaign-* ]]; then export SKILLS_CAMPAIGN_PREFLIGHT=1; fi
    bin/e2e test --grep "$pattern" --reporter=json --trace off || rc=$?
  fi
  [[ $(git rev-parse HEAD) == "${GITHUB_SHA:?}" ]]
  python3 -I "$coverage" receipt "$tmp/results.json" "$cohort" "$GITHUB_SHA" "$RUNNER_TEMP/$cohort.json"
  exit "$rc"
fi
