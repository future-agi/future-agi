#!/usr/bin/env bash
# Permanent round-three behavioral probes. Run from the repository root on a VM.
set -euo pipefail
export GOMAXPROCS=${GOMAXPROCS:-32}
export TEST_REDIS_ADDR=${TEST_REDIS_ADDR:-127.0.0.1:16380} TEST_REDIS_DB=${TEST_REDIS_DB:-11}
cd agentcc-gateway
for mode in plain race; do
  flags=(); test "$mode" != race || flags=(-race)
  go test "${flags[@]}" -p 32 -timeout 20m -count=1 -json -run '^TestSol|^TestMixed' ./internal/server ./internal/redisstate ./internal/billing ./internal/plugins/credits
 done
