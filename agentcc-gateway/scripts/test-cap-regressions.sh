#!/usr/bin/env bash
# Cloud-only: permanent counterpart of the independent overlay reproducer.
# Uses synthetic Redis and in-repository tests; no production/provider access.
set -euo pipefail
: "${TEST_REDIS_ADDR:?synthetic Redis address required}"
: "${TEST_REDIS_DB:?synthetic Redis database required}"
: "${CAP_TEST_OUTPUT:?absolute evidence directory required}"
case "$CAP_TEST_OUTPUT" in /*) ;; *) exit 2 ;; esac
ROOT=$(git rev-parse --show-toplevel)
mkdir -p "$CAP_TEST_OUTPUT"
export OLLAMA_HOST=127.0.0.1:9 OLLAMA_URL=http://127.0.0.1:9 OLLAMA_BASE_URL=http://127.0.0.1:9
export FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1
cd "$ROOT/agentcc-gateway"
names='TestCapValidRollbackVersionRemainsUsable|TestCapRunCrashDoesNotEraseUnknownSpend|TestCapPolicyChangesRemainFencedAcrossUTCDate|TestCapBYOKUnknownUsageCannotReopenCapacity|TestR2IncompleteUsageRetainsReservation|TestR2UnknownMatrixAndUnsupportedStreaming|TestR2ExplicitZeroAndReportedChargeRemainUsable|TestR2StrictRunProcessCrashAndLateActual|TestR2MalformedDailyCounterFailsClosed|TestR2StrictRunAmbiguousRepliesRemainFailClosed'
# Regression reproductions and validation-before-mutation controls.
# Native-adapter presence is covered below after adoption of the verified adapters.
names+='|TestR3StrictRunMalformedCounters|TestR3StrictRunMissingLimitFailsClosed|TestR3DailyMalformedExtraFields|TestR3ReportedChargeBeyondLedgerRange|TestR3StrictRunNonIntegerSpentEndToEnd|TestStrictRunMalformedCountersNeverMutate|TestStrictRunPreflightBeforeCounterWrites|TestStrictRunCanonicalLegacyAdoption'
# Round-four reproductions: completion preflight before release, fenceable
# refused completions, and wrong-type auxiliary keys in both cap ledgers.
names+='|TestR4DailyFailedCompleteRetainsFenceableReservation|TestR4MalformedRunAuxiliaryStateNeverMutates|TestR4DailyCompletionFailureActuallyFences|TestCapRefusedCompleteSettlesAfterRepair|TestCapDailyCompletionPreflight|TestCapDailyWrongTypeKeysNeverMutate|TestStrictRunWrongTypeKeysNeverMutate|TestStrictRunConflictingReplayNeverMutates|TestStrictRunWrongTypeAuxiliaryStateEndToEnd'
# Round-five reproductions: strict fences that read no counter, fence evidence
# behind a lost or zero daily uncertainty counter, and activation checked
# before the expiry sweep and the replay fast path.
names+='|TestR5MissingFencedUncertaintyNeverAdmits|TestR5ActivationValidationBeforeExpiryFencing|TestR5SecondBucketPreflightNeverMutates|TestR5StrictRefusedCompleteRemainsFenceable|TestR5StoppedStringIsAValidMarker|TestStrictFenceNeverReadsCounters|TestStrictFenceIgnoresKeysItNeverTouches|TestStrictFenceSurvivesLostAdoptionMarker|TestCapFenceRecountsLostUncertainty|TestCapFencedCompleteSelfHealsLostUncertainty|TestCapFenceEvidenceIntegrityRefusesAdmission|TestCapFenceOnOtherBucketDoesNotBlock|TestCapActivationMismatchRefusesBeforeSweepAndReplay|TestStrictRunMalformedSpentCompletionFencesEndToEnd|TestCapDailyLostUncertaintyCounterEndToEnd'
# Round-six probes and native provenance in both ledgers, with explicit-zero
# controls and the constant-time orphaned-hold admission invariant.
names+='|TestR3NativeAdapterUsageProvenance|TestR3ManagedNativeAdapterUsageProvenance|TestR6StrictLostUncertaintyNeverReopens|TestR6DailyUnderCountNeverReopens|TestR6ActivationSecondBucketNeverMutates|TestR6StaleMissingUncertaintyNeverMutates|TestR6StrictLostUncertaintySetEndToEnd|TestStrictOrphanHoldRefusesPeekAndReserve|TestStrictPendingIndexLifecycle'
go version >"$CAP_TEST_OUTPUT/toolchain.txt"
failed=0
for mode in plain race; do
 flags=(); if [ "$mode" = race ]; then flags=(-race); fi
 if go test -json "${flags[@]}" -p "${GOMAXPROCS:-32}" -count=1 -timeout=10m -run "^($names)$" ./internal/server ./internal/redisstate >"$CAP_TEST_OUTPUT/$mode.log" 2>"$CAP_TEST_OUTPUT/$mode.stderr"; then
  printf '%s\t0\n' "$mode" >>"$CAP_TEST_OUTPUT/codes.tsv"
 else
  rc=$?; printf '%s\t%s\n' "$mode" "$rc" >>"$CAP_TEST_OUTPUT/codes.tsv"; failed=1
 fi
done
exit "$failed"
