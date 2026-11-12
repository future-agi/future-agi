#!/usr/bin/env bash
# Cloud-only permanent baseline from the independent round-two verifier.
# Run Django cap/config tests first with G8_CONFIG_ACTIVATION_FIXTURE exported;
# the Go activation-to-chat test must consume that real config-writer fixture.
set -euo pipefail
: "${TEST_REDIS_ADDR:?synthetic Redis address required}"
: "${TEST_REDIS_DB:?synthetic Redis database required}"
: "${G8_CONFIG_ACTIVATION_FIXTURE:?Django activation fixture required}"
: "${CAP_TEST_OUTPUT:?absolute evidence directory required}"
case "$CAP_TEST_OUTPUT" in /*) ;; *) exit 2 ;; esac
test -s "$G8_CONFIG_ACTIVATION_FIXTURE"
ROOT=$(git rev-parse --show-toplevel)
mkdir -p "$CAP_TEST_OUTPUT"
export OLLAMA_HOST=127.0.0.1:9 OLLAMA_URL=http://127.0.0.1:9 OLLAMA_BASE_URL=http://127.0.0.1:9
export FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1
cd "$ROOT/agentcc-gateway"
go version >"$CAP_TEST_OUTPUT/toolchain.txt"
failed=0
for mode in plain race; do
 flags=(); if [ "$mode" = race ]; then flags=(-race); fi
 if go test -json "${flags[@]}" -p "${GOMAXPROCS:-32}" -count=1 -timeout=20m -run 'Test(Cap|StrictRun|R2|Capped)' ./internal/plugins/budget ./internal/plugins/agentbudget ./internal/server ./internal/redisstate >"$CAP_TEST_OUTPUT/$mode.log" 2>"$CAP_TEST_OUTPUT/$mode.stderr"; then
  printf '%s\t0\n' "$mode" >>"$CAP_TEST_OUTPUT/codes.tsv"
 else
  rc=$?; printf '%s\t%s\n' "$mode" "$rc" >>"$CAP_TEST_OUTPUT/codes.tsv"; failed=1
 fi
done
exit "$failed"
