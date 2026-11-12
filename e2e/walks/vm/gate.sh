#!/usr/bin/env bash
# H16 gate job (cloud VM only): the cheap checks before any stack is built.
#   yarn typecheck, yarn catalog (regenerates e2e/FLOWS.md, pulled back for the
#   commit) then yarn catalog:check, yarn selftest (scripts/*.test.mjs), and the
#   offline halves of the harness self-tests (no stack: --grep offline).
# Usage: gate.sh <expected HEAD>. Writes e2e/walk-results/gate/. Exit 0 only
# when every step passed. Never yarn prettier.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'cloud VM only'; exit 70; }
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
EXPECT=${1:?usage: gate.sh <expected HEAD>}
OUT=$ROOT/e2e/walk-results/gate
mkdir -p "$OUT"
cd "$ROOT"
[[ $(git rev-parse HEAD) == "$EXPECT" ]] || { echo "HEAD is not $EXPECT"; exit 71; }
[[ -z $(git --no-optional-locks status --porcelain --untracked-files=no) ]] || { git status --short | head; exit 72; }
export CI=1 PLAYWRIGHT_BROWSERS_PATH=$HOME/ms-pw
{ echo "realHead=$(git rev-parse HEAD)"; echo "dirty=0"; date -u +%FT%TZ; node --version; nproc
  echo "dockerHost=${DOCKER_HOST:-unset}"; docker context ls; docker version --format '{{.Server.Version}}'
  command -v go uv yarn; } > "$OUT/source.txt" 2>&1
cd e2e
fail=0
step() {
  local name=$1; shift
  local start=$SECONDS rc=0
  "$@" > "$OUT/$name.log" 2>&1 || rc=$?
  printf '%s rc=%s secs=%s\n' "$name" "$rc" "$((SECONDS - start))" | tee -a "$OUT/steps.txt"
  ((rc == 0)) || fail=1
}
step typecheck yarn typecheck
step catalog yarn catalog
step catalog-check yarn catalog:check
step selftest yarn selftest
# The offline self-tests (no stack): the walk harness and profiles (`offline` in
# the title) and the inspector's fake-Docker cases (`managed mock ...`; its two live
# cases are titled `managed stack ...` / `managed background ...`).
step harness-offline env E2E_WALK_COUNTS="$OUT/counts.json" yarn playwright test \
  harness/walk-harness.spec.ts harness/walk-money.spec.ts harness/walk-contract-sources.spec.ts harness/managed-mock.spec.ts harness/gateway-profile.spec.ts \
  harness/profile-compat.spec.ts harness/scope-compat.spec.ts harness/walk-regressions.spec.ts harness/gateway-admin.spec.ts harness/landing-fixtures.spec.ts \
  harness/trace-hygiene-offline.spec.ts harness/trace-hygiene-controls.spec.ts --grep 'offline|managed mock' \
  --reporter=line,./walks/vm/counts-reporter.cjs --workers=4
# Node's numeric TAP footer only; no fixture values or failure diagnostics.
grep -E '^# (tests|suites|pass|fail|cancelled|skipped|todo) [0-9]+$' "$OUT/selftest.log" >> "$OUT/steps.txt" || true
# Compiler locations/codes only: never pull diagnostic literals or raw logs.
node - "$OUT" <<'NODE'
const fs = require('node:fs');
const out = process.argv[2];
const diagnostics = fs.readFileSync(out + '/typecheck.log', 'utf8').split('\n').flatMap(line => {
  const m = /^([A-Za-z0-9_./-]+)\((\d+),(\d+)\): error (TS\d+):/.exec(line);
  return m ? [{ file: m[1], line: Number(m[2]), column: Number(m[3]), code: m[4] }] : [];
});
fs.writeFileSync(out + '/summary.json', JSON.stringify({ compilerDiagnostics: diagnostics }, null, 2) + '\n');
NODE
git -C "$ROOT" diff --stat -- e2e/FLOWS.md > "$OUT/flows-diff.txt"
exit "$fail"
