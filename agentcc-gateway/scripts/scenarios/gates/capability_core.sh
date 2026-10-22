#!/usr/bin/env bash
# Integration gate for the capability core.
#
# Proves, on this tree:
#   1. Named Go tests report --- PASS by name, so a renamed or skipped test
#      cannot pass by matching nothing: the capability catalog, capability
#      core, embeddings, server-tool, egress and probe acceptance tests, and
#      the wiring and integration rows. The capability core runs on the test
#      Redis (TEST_REDIS_ADDR) under -race.
#   2. Scenarios: 06_capmock.sh, 07_capability_core.sh, 10_embeddings.sh,
#      04_server_tools.sh (a BYOK Anthropic fixture with
#      web_search_requests=3 estimates $0.03 plus tokens) and 71_probe.sh,
#      then managed_tools_live_rows.sh (the managed outbox spend record, the
#      BYOK estimate, managed_tools off, and the startup refusals: a byok_only
#      managed vendor, a dangling price_card, a non-finite tool markup and
#      mcp.endpoint /).
#   3. The release integrity check: bash agentcc-gateway/scripts/release/verify-local.sh.
#   4. Django (GATE_DJANGO_TEST, a test-runner script): the tools org-config
#      files and their write-path rows.
#   5. Unless GATE_SKIP_EARLIER=1: every earlier gate, mcp_seams.sh with the
#      benchmark baseline BENCH_BASELINE (allocs budget 0; the maintainer
#      supplies the base commit), which runs call_order_seams.sh and the earlier checks.
#
# Usage: capability_core.sh   (set BENCH_BASELINE to the base commit unless GATE_SKIP_EARLIER=1)
# Environment: SCN_REDIS, SCN_REDIS_DB (required); BENCH_BASELINE, and PIN and
# MOVE_COMMIT for mcp_seams.sh (required unless GATE_SKIP_EARLIER=1); GATE_HOME;
# GATE_DJANGO_TEST and GATE_DJANGO_MANAGE (mcp_seams.sh's parts 4 and 5 need both);
# GATE_SKIP_EARLIER.
# Exit status: 0 when every part passes, 1 otherwise.
set -u

BENCH_BASELINE=${BENCH_BASELINE:-}
if [ -z "$BENCH_BASELINE" ] && [ "${GATE_SKIP_EARLIER:-}" != 1 ]; then
  echo "usage: capability_core.sh (set BENCH_BASELINE to the base commit, or GATE_SKIP_EARLIER=1)" >&2
  exit 1
fi
GATES=$(cd "$(dirname "$0")" && pwd)
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
REPO=$(cd "$GW/.." && pwd)
GATE_HOME=${GATE_HOME:-${TMPDIR:-/tmp}/agentcc-gates}
SCN_TIMEOUT=${GATE_SCENARIO_TIMEOUT:-1200}
mkdir -p "$GATE_HOME"

if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "capability_core: SCN_REDIS and a non-zero SCN_REDIS_DB are required" >&2
  exit 1
fi

status=0
join() { local IFS='|'; echo "$*"; }
expect_passed() { # <log> <names...>
  local log=$1 name missing=0
  shift
  for name in "$@"; do
    if ! grep -Eq -- "^\s*--- PASS: ${name} " "$log"; then
      echo "  missing PASS for $name"
      missing=1
    fi
  done
  return $missing
}

# --- part 1: named Go tests -------------------------------------------------
echo "### capability_core part 1: named Go tests (acceptance and integration rows)"
CATALOG_TESTS=(TestByokOnlyManagedRejected TestPriceCardSchema TestSeedCardsValid TestTermsCheckedAtPresent TestTermsFlagsRequired
  TestExaNeverCacheable TestManagedServerPriceCardResolves TestDanglingPriceCardIsStartupError
  TestDelegatedServerWithoutCardResolves TestManagedServerIDNormalized TestGlobCasesArePathMatch)
CORE_TESTS=(TestByokFirst TestManagedOnlyWhenManagedToolsAllows TestByokOnlyNeverManaged TestNoByokToManagedWithoutFallbackToManaged
  TestOwnerPricePendingRefused503 TestNoDivertOnlyWhenPinned TestNoDivertNeverFallsBack TestNoPerfDisclosureNotInResponse
  TestZdrFilter TestNoTrainingFilter TestImprovementLicenseExcludedForNoTraining TestRegionFailsClosed TestFallbackClasses
  TestFourCandidateCap TestReserveSettleExactMicros TestVoidWhenVendorDoesNotBillFailure TestSubMicroAccrual TestCacheOrgOnly
  TestCacheHitFree TestRetentionMostRestrictive TestEvalExportFlag TestBillingMetaForMCP TestWorstCaseMicrosIsTheReserve
  TestCardSourceFixturesMatchGolden TestEchoManagedSettles1050AndRefusesTenth TestEchoByokFreeNoOutbox
  TestEchoVendorFallbackTwoAttempts TestZeroHoldManagedCallSettlesWhatItBills TestManagedCallFailsClosedBeforeTheVendor
  TestFractionalUnitsNeverPriceBelowOneUnit TestAttemptIndicesNeverRepeatOnARequest TestNoPerfDisclosureFlagged
  TestMetadataRequestKeepsNoBodyHash TestPanickingAdapterHoldIsVoided)
CORE_ROUTE_TESTS=(TestCapabilityAttemptAttributed TestCapabilityCredits402Golden TestManagedCapabilityUnavailable503
  TestEchoRouteOnlyWhenConfigured TestCapabilityNotUnderTheKeyModelAllowlist TestEchoUnitsMustBeWhole
  TestCapabilityNeverChargesAPrepaidKey TestResaleTermsNativeAndByokOnly TestCapabilityRowsFromRegistry
  TestScheduledAndVideosUnsupported TestCapabilityPriceFieldAdditive TestResaleTerms TestCapabilitiesGolden
  TestCustomerCapabilitiesOmitResaleTerms)
EMBEDDING_TESTS=(TestCohereQueryInputTypePerRequest TestCohereHeaderDefaultWhenNoInputType TestCohereRerankBilledUnits
  TestGeminiUsageEstimatedNotZero TestGeminiTaskTypeFromInputType TestGeminiTransportErrorsHideAPIKey
  TestEmbeddingsCallerMetadataApplied TestRerankCallerMetadataApplied TestInputTypeUnsupported400 TestSpaceHeader
  TestEmbeddingsNoCrossHostFailover TestByokEmbeddingsFreeMetered TestRerankOrgKeyAttemptIsByok
  TestEmbeddingNilResponseIsAFailedAttempt TestEmbeddingsNeverResolveToAFallbackModel)
SERVER_TOOL_TESTS=(TestAnthropicWebSearchBilled TestOpenAIWebSearchCallBilled TestOpenAIMiniFixedBlock TestGemini3QueriesBilled
  TestGemini25GroundedPrompts TestWebFetchFree TestOpenAIContainerSessionBilled TestOpenAIContainerFiveMinuteMinimum
  TestAnthropicCodeExecutionBilled TestAnthropicCodeExecutionFreeWithNewSearch TestUpstreamReportedWins TestByokFeeIsEstimate
  TestManagedServerToolFeeWithMarkup TestGeminiGroundingSetsEvalExportForbidden TestWebSearchNeverToBedrock
  TestWebSearchAllowedOnClaudePlatformAWS TestNewSearchVersionsNotToGoogleCloudOrAzureFoundry TestWebSearch20260318Detected
  TestWebFetchNotToBedrockOrGoogle TestCodeExecutionNotToBedrockGoogleOrAzureFoundry TestEncryptedContentRoundTrip
  TestSearchEntryPointPreserved TestCardWebSearchPriceKeepsMiniFixedBlock TestServerToolRouteUsesOnlyCredentialedOrgConfig
  TestRuleOrder)
PROBE=(TestDialerRefusesPrivateAndMetadata TestRedirectRechecked TestHTTPSRequired TestPrivateAllowOperatorListOnly
  TestMetadataRefusedEvenWhenListed TestOrgConfigCannotOpenPrivate TestProbeReportsEraToolsHash TestProbeNotMCP
  TestEgressUsesNetguardClassification TestProbeInlineAuthNeverLogged TestPrivateAllowWideRangeKeepsMetadataRefused
  TestProbeParamsStructuredOrOmitted)
WIRING_ROWS=(TestWiringOrder TestRouteStubsRegistered TestBillableHandlersRunThePipeline TestMCPWiringOrder
  TestCapabilityWiringDanglingPriceCardFailsStartup TestCapabilityWiringManagedByokOnlyVendorFailsStartup
  TestCapabilityWiringCardSourceIsTheCatalog TestNonFiniteMarkupIsConfigError TestMCPEndpointCollidingWithAPIRootIsConfigError
  TestAPIRouteRootsCoverEveryRoute TestRerankSearchUnitsMetadataKeyReserved TestNonChatRequestTakesNoModelFallback
  TestNonChatRouteSendsTheRoutedModel TestNativeGenAIGroundingMetered TestNativeMessagesServerToolsMetered
  TestGroundedResponseNeverCached TestNativeGenAIGroundingMarksEvalExportForbidden
  TestCapabilityServedCallLogsStatus200)
p1=$GATE_HOME/capability-core-part1-go.log
p1r=$GATE_HOME/capability-core-part1-race.log
REST=("${CATALOG_TESTS[@]}" "${CORE_ROUTE_TESTS[@]}" "${EMBEDDING_TESTS[@]}" "${SERVER_TOOL_TESTS[@]}" "${PROBE[@]}" "${WIRING_ROWS[@]}")
(cd "$GW" && env -u AGENTCC_MCP_STATE_KEYS go test ./... -run "^($(join "${REST[@]}"))\$" -count=1 -v &&
  go test ./cmd/agentcc-capmock/... ./catalog/... -count=1) >"$p1" 2>&1
rc1=$?
(cd "$GW" && TEST_REDIS_ADDR=${TEST_REDIS_ADDR:-$SCN_REDIS} TEST_REDIS_DB=$SCN_REDIS_DB \
  go test -race ./internal/capability/... -run "^($(join "${CORE_TESTS[@]}"))\$" -count=1 -v) >"$p1r" 2>&1
rc2=$?
if [ $rc1 -eq 0 ] && [ $rc2 -eq 0 ] && expect_passed "$p1" "${REST[@]}" && expect_passed "$p1r" "${CORE_TESTS[@]}" &&
  ! grep -q -- "--- SKIP" "$p1r"; then
  echo "capability_core part 1: PASS ($((${#REST[@]} + ${#CORE_TESTS[@]})) named tests, none skipped on Redis)"
else
  echo "capability_core part 1: FAIL (exit $rc1/$rc2; logs $p1 $p1r)"
  grep -E -- "^(--- FAIL|FAIL|panic)" "$p1" "$p1r" | head -20
  status=1
fi

# --- part 2: scenarios -------------------------------------------------------
echo "### capability_core part 2: 06, 07, 10, 04, 71 and managed_tools_live_rows"
SCN_RUN=${SCN_RUN:-gate-capability} SCN_HOME=${SCN_HOME:-$GATE_HOME/scn} \
  perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$SCN_TIMEOUT" \
  bash "$SC/run_all.sh" 06_capmock.sh 07_capability_core.sh 10_embeddings.sh 04_server_tools.sh 71_probe.sh
rc=$?
if [ $rc -eq 0 ]; then echo "capability_core part 2a: PASS"; else echo "capability_core part 2a: FAIL (exit $rc)"; status=1; fi
if GATE_HOME=$GATE_HOME bash "$GATES/managed_tools_live_rows.sh"; then
  echo "capability_core part 2b: PASS"
else
  echo "capability_core part 2b: FAIL"
  status=1
fi

# --- part 3: the release integrity check ---
echo "### capability_core part 3: verify-local.sh"
p3=$GATE_HOME/capability-core-part3-verify-local.log
if (cd "$REPO" && bash agentcc-gateway/scripts/release/verify-local.sh) >"$p3" 2>&1 &&
  grep -q "verify-local: all checks pass" "$p3"; then
  echo "capability_core part 3: PASS ($(grep -c '^PASS ' "$p3") checks)"
else
  echo "capability_core part 3: FAIL (log $p3)"
  tail -12 "$p3"
  status=1
fi

# --- part 4: Django rows ------------------------------------------------------
echo "### capability_core part 4: Django rows (tools org config, the write-path rows)"
p4=$GATE_HOME/capability-core-part4-django.log
if [ -z "${GATE_DJANGO_TEST:-}" ]; then
  echo "capability_core part 4: FAIL (GATE_DJANGO_TEST is not set)"
  status=1
else
  "$GATE_DJANGO_TEST" agentcc/tests/test_org_config_tools.py agentcc/tests/test_org_config_tools_write_paths.py \
    agentcc/tests/test_org_config_mcp_write_rules.py agentcc/tests/test_org_config_tools_identity_storage.py \
    -p no:cacheprovider -q -rA >"$p4" 2>&1
  rc=$?
  missing=0
  for t in test_multipart_save_naming_a_byok_only_vendor_is_400 test_update_config_refuses_a_byok_only_managed_glob \
    test_update_config_reruns_validate_tools test_byok_only_refusal_is_keyed_under_mcp \
    test_byok_only_refused_when_mcp_arrives_as_a_json_string test_managed_tools_globs_checked_under_every_spelling \
    test_tools_block_round_trips test_byok_only_vendor_managed_400; do
    grep -q "^PASSED .*::$t" "$p4" || { echo "  missing PASSED for $t"; missing=1; }
  done
  if [ $rc -eq 0 ] && [ $missing -eq 0 ]; then
    echo "capability_core part 4: PASS ($(grep -Eo '[0-9]+ passed' "$p4" | tail -1))"
  else
    echo "capability_core part 4: FAIL (exit $rc; log $p4)"
    status=1
  fi
fi

# --- part 5: every earlier gate -------------------------------------------------
if [ "${GATE_SKIP_EARLIER:-}" = 1 ]; then
  echo "### capability_core part 5: skipped (GATE_SKIP_EARLIER=1; run the earlier gates separately)"
else
  echo "### capability_core part 5: mcp_seams.sh $BENCH_BASELINE (every earlier gate)"
  if GATE_HOME=$GATE_HOME bash "$GATES/mcp_seams.sh" "$BENCH_BASELINE"; then
    echo "capability_core part 5: PASS"
  else
    echo "capability_core part 5: FAIL"
    status=1
  fi
fi

if [ $status -eq 0 ]; then echo "capability_core: PASS"; else echo "capability_core: FAIL"; fi
exit $status
