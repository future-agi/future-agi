#!/usr/bin/env bash
# Replay the additional provenance regression against the pre-fix commit.
set -euo pipefail
BASE=${1:?provide the pre-fix commit}
OUT=${2:?provide evidence directory}
ROOT=$(pwd)
TMP=$(mktemp -d /tmp/onekey-provenance.XXXXXX)
mkdir -p "$OUT"
git archive "$BASE" agentcc-gateway | tar -x -C "$TMP"
cp "$ROOT/agentcc-gateway/internal/billing/mixed_provenance_test.go" "$TMP/agentcc-gateway/internal/billing/"
for mode in plain race; do
 flags=(); test "$mode" != race || flags=(-race)
 set +e
 (cd "$TMP/agentcc-gateway" && go test "${flags[@]}" -count=1 -run '^TestMixedRecoveryPreservesUnresolvedAttemptProvenance$' ./internal/billing) >"$OUT/provenance-red-$mode.log" 2>&1
 result=$?
 set -e
 test "$result" = 1
 grep -q 'recovery replaced prior provenance' "$OUT/provenance-red-$mode.log"
 echo "provenance-red-$mode: expected behavioral failure confirmed"
done
