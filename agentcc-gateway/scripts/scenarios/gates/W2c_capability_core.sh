#!/usr/bin/env bash
# W2c wave-gate integration check (plan section 3, integrator-owned).
#
# Proves, on the integrated W2c tree:
#   1. Named Go tests report --- PASS by name (duty 3), so a renamed or
#      skipped test cannot pass by matching nothing: every W2c lane's named
#      acceptance tests (V4, V5 with its review regressions, V8a, V2, M17) and
#      the integrator's rows (SR6, SR7, IC-7, and the integrator requests
#      landed at the merge). V5's capability core runs on the test Redis
#      (TEST_REDIS_ADDR) under -race, as in its lane, and so does the Redis
#      cache backend case (TEST_CACHE_REDIS_ADDR, TEST_CACHE_REDIS_DB): a
#      response cached for one org is never served to another org or to a
#      request with no org (TestBackendMatrix_ExactNeverCrossesOrgs/redis,
#      which must PASS, not skip).
#   2. Scenarios: 06_v4_capmock.sh, 07_v5_capability_core.sh (on V4's capmock
#      and SR7's binding), 10_v8a_embeddings_fixes.sh, 04_v2_server_tools.sh
#      (a BYOK Anthropic fixture with web_search_requests=3 estimates $0.03
#      plus tokens) and 71_m17_probe.sh, then W2c_live_rows.sh (the managed
#      outbox spend record, the BYOK estimate, managed_tools off, and the
#      startup refusals: a byok_only managed vendor, a dangling price_card and
#      IC-7's .nan tool markup and mcp.endpoint /).
#   3. P6: bash agentcc-gateway/scripts/release/verify-local.sh.
#   4. Django (GATE_DJANGO_TEST, a lane-test script): V4's acceptance file and
#      the integrator's write-path rows (V4 review P1, IR-5, IR-6, IR-7).
#   5. Unless GATE_SKIP_EARLIER=1: every earlier gate, W2b_mcp_seams.sh with
#      the benchmark baseline 2786e6c7ff (the merged W2b tree, allocs budget
#      0; C221), which runs W0b_seams.sh and the W1 and W2 checks.
#
# Usage: W2c_capability_core.sh
# Environment: SCN_REDIS, SCN_REDIS_DB (required; TEST_REDIS_ADDR and then
# TEST_CACHE_REDIS_ADDR default to SCN_REDIS, both on DB SCN_REDIS_DB); GATE_HOME; GATE_DJANGO_TEST
# and GATE_DJANGO_MANAGE (W2b's parts 4 and 5 need both); GATE_SKIP_EARLIER.
# Exit status: 0 when every part passes, 1 otherwise.
set -u

BENCH_BASELINE=2786e6c7ff1c7900d20e0dd208a72b934757d2f4
GATES=$(cd "$(dirname "$0")" && pwd)
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
REPO=$(cd "$GW/.." && pwd)
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
SCN_TIMEOUT=${GATE_SCENARIO_TIMEOUT:-1200}
mkdir -p "$GATE_HOME"

if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "W2c_capability_core: SCN_REDIS and a non-zero SCN_REDIS_DB are required" >&2
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
echo "### W2c_capability_core part 1: named Go tests (lane acceptance, integrator rows)"
V4=(TestByokOnlyManagedRejected TestPriceCardSchema TestSeedCardsValid TestTermsCheckedAtPresent TestTermsFlagsRequired
  TestExaNeverCacheable TestManagedServerPriceCardResolves TestDanglingPriceCardIsStartupError
  TestDelegatedServerWithoutCardResolves TestManagedServerIDNormalized TestGlobCasesArePathMatch)
V5_CORE=(TestByokFirst TestManagedOnlyWhenManagedToolsAllows TestByokOnlyNeverManaged TestNoByokToManagedWithoutFallbackToManaged
  TestOwnerPricePendingRefused503 TestNoDivertOnlyWhenPinned TestNoDivertNeverFallsBack TestNoPerfDisclosureNotInResponse
  TestZdrFilter TestNoTrainingFilter TestImprovementLicenseExcludedForNoTraining TestRegionFailsClosed TestFallbackClasses
  TestFourCandidateCap TestReserveSettleExactMicros TestVoidWhenVendorDoesNotBillFailure TestSubMicroAccrual TestCacheOrgOnly
  TestCacheHitFree TestRetentionMostRestrictive TestEvalExportFlag TestBillingMetaForMCP TestWorstCaseMicrosIsTheReserve
  TestCardSourceFixturesMatchGolden TestEchoManagedSettles1050AndRefusesTenth TestEchoByokFreeNoOutbox
  TestEchoVendorFallbackTwoAttempts TestZeroHoldManagedCallSettlesWhatItBills TestManagedCallFailsClosedBeforeTheVendor
  TestFractionalUnitsNeverPriceBelowOneUnit TestAttemptIndicesNeverRepeatOnARequest TestNoPerfDisclosureFlagged
  TestMetadataRequestKeepsNoBodyHash TestPanickingAdapterHoldIsVoided)
V5_REST=(TestCapabilityAttemptAttributed TestCapabilityCredits402Golden TestManagedCapabilityUnavailable503
  TestEchoRouteOnlyWhenConfigured TestCapabilityNotUnderTheKeyModelAllowlist TestEchoUnitsMustBeWhole
  TestCapabilityNeverChargesAPrepaidKey TestResaleTermsNativeAndByokOnly TestCapabilityRowsFromRegistry
  TestScheduledAndVideosUnsupported TestCapabilityPriceFieldAdditive TestResaleTerms TestCapabilitiesGolden
  TestCustomerCapabilitiesOmitResaleTerms)
V8A=(TestCohereQueryInputTypePerRequest TestCohereHeaderDefaultWhenNoInputType TestCohereRerankBilledUnits
  TestGeminiUsageEstimatedNotZero TestGeminiTaskTypeFromInputType TestGeminiTransportErrorsHideAPIKey
  TestEmbeddingsCallerMetadataApplied TestRerankCallerMetadataApplied TestInputTypeUnsupported400 TestSpaceHeader
  TestEmbeddingsNoCrossHostFailover TestByokEmbeddingsFreeMetered TestRerankOrgKeyAttemptIsByok
  TestEmbeddingNilResponseIsAFailedAttempt TestEmbeddingsNeverResolveToAFallbackModel)
V2=(TestAnthropicWebSearchBilled TestOpenAIWebSearchCallBilled TestOpenAIMiniFixedBlock TestGemini3QueriesBilled
  TestGemini25GroundedPrompts TestWebFetchFree TestOpenAIContainerSessionBilled TestOpenAIContainerFiveMinuteMinimum
  TestAnthropicCodeExecutionBilled TestAnthropicCodeExecutionFreeWithNewSearch TestUpstreamReportedWins TestByokFeeIsEstimate
  TestManagedServerToolFeeWithMarkup TestGeminiGroundingSetsEvalExportForbidden TestWebSearchNeverToBedrock
  TestWebSearchAllowedOnClaudePlatformAWS TestNewSearchVersionsNotToGoogleCloudOrAzureFoundry TestWebSearch20260318Detected
  TestWebFetchNotToBedrockOrGoogle TestCodeExecutionNotToBedrockGoogleOrAzureFoundry TestEncryptedContentRoundTrip
  TestSearchEntryPointPreserved TestCardWebSearchPriceKeepsMiniFixedBlock TestServerToolRouteUsesOnlyCredentialedOrgConfig
  TestRuleOrder)
M17=(TestDialerRefusesPrivateAndMetadata TestRedirectRechecked TestHTTPSRequired TestPrivateAllowOperatorListOnly
  TestMetadataRefusedEvenWhenListed TestOrgConfigCannotOpenPrivate TestProbeReportsEraToolsHash TestProbeNotMCP
  TestEgressUsesNetguardClassification TestProbeInlineAuthNeverLogged TestPrivateAllowWideRangeKeepsMetadataRefused
  TestProbeParamsStructuredOrOmitted)
INTEGRATOR=(TestWiringOrder TestRouteStubsRegistered TestBillableHandlersRunThePipeline TestMCPWiringOrder
  TestCapabilityWiringDanglingPriceCardFailsStartup TestCapabilityWiringManagedByokOnlyVendorFailsStartup
  TestCapabilityWiringCardSourceIsTheCatalog TestNonFiniteMarkupIsConfigError TestMCPEndpointCollidingWithAPIRootIsConfigError
  TestAPIRouteRootsCoverEveryRoute TestRerankSearchUnitsMetadataKeyReserved TestNonChatRequestTakesNoModelFallback
  TestNonChatRouteSendsTheRoutedModel TestNativeGenAIGroundingMetered TestNativeMessagesServerToolsMetered
  TestGroundedResponseNeverCached TestNativeGenAIGroundingMarksEvalExportForbidden
  TestCapabilityServedCallLogsStatus200)
p1=$GATE_HOME/w2c-part1-go.log
p1r=$GATE_HOME/w2c-part1-capability-race.log
REST=("${V4[@]}" "${V5_REST[@]}" "${V8A[@]}" "${V2[@]}" "${M17[@]}" "${INTEGRATOR[@]}")
(cd "$GW" && env -u AGENTCC_MCP_STATE_KEYS go test ./... -run "^($(join "${REST[@]}"))\$" -count=1 -v &&
  go test ./cmd/agentcc-capmock/... ./catalog/... -count=1) >"$p1" 2>&1
rc1=$?
# The Redis cache backend case reads only TEST_CACHE_REDIS_ADDR and
# TEST_CACHE_REDIS_DB (opt-in, apart from TEST_REDIS_ADDR); without them it
# skips, which the SKIP check below turns into a FAIL.
CACHE_REDIS=(TestBackendMatrix_ExactNeverCrossesOrgs TestBackendMatrix_ExactNeverCrossesOrgs/redis)
(cd "$GW" && export TEST_REDIS_ADDR=${TEST_REDIS_ADDR:-$SCN_REDIS} TEST_REDIS_DB=$SCN_REDIS_DB &&
  export TEST_CACHE_REDIS_ADDR=${TEST_CACHE_REDIS_ADDR:-$TEST_REDIS_ADDR} TEST_CACHE_REDIS_DB=$SCN_REDIS_DB &&
  go test -race ./internal/capability/... -run "^($(join "${V5_CORE[@]}"))\$" -count=1 -v &&
  go test -race ./internal/plugins/cache/ -run '^TestBackendMatrix_ExactNeverCrossesOrgs$' -count=1 -v) >"$p1r" 2>&1
rc2=$?
if [ $rc1 -eq 0 ] && [ $rc2 -eq 0 ] && expect_passed "$p1" "${REST[@]}" &&
  expect_passed "$p1r" "${V5_CORE[@]}" "${CACHE_REDIS[@]}" && ! grep -q -- "--- SKIP" "$p1r"; then
  echo "W2c_capability_core part 1: PASS ($((${#REST[@]} + ${#V5_CORE[@]})) named tests and the Redis cache backend case, none skipped on Redis)"
else
  echo "W2c_capability_core part 1: FAIL (exit $rc1/$rc2; logs $p1 $p1r)"
  grep -E -- "^(--- FAIL|FAIL|panic)" "$p1" "$p1r" | head -20
  status=1
fi

# --- part 2: scenarios -------------------------------------------------------
echo "### W2c_capability_core part 2: 06, 07, 10, 04, 71 and W2c_live_rows"
SCN_LANE=${SCN_LANE:-x6-w2c-gate} SCN_HOME=${SCN_HOME:-$GATE_HOME/scn} \
  perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$SCN_TIMEOUT" \
  bash "$SC/run_all.sh" 06_v4_capmock.sh 07_v5_capability_core.sh 10_v8a_embeddings_fixes.sh 04_v2_server_tools.sh 71_m17_probe.sh
rc=$?
if [ $rc -eq 0 ]; then echo "W2c_capability_core part 2a: PASS"; else echo "W2c_capability_core part 2a: FAIL (exit $rc)"; status=1; fi
if GATE_HOME=$GATE_HOME bash "$GATES/W2c_live_rows.sh"; then
  echo "W2c_capability_core part 2b: PASS"
else
  echo "W2c_capability_core part 2b: FAIL"
  status=1
fi

# --- part 3: P6's release integrity check -------------------------------------
echo "### W2c_capability_core part 3: verify-local.sh (P6)"
p3=$GATE_HOME/w2c-part3-verify-local.log
if (cd "$REPO" && bash agentcc-gateway/scripts/release/verify-local.sh) >"$p3" 2>&1 &&
  grep -q "verify-local: all checks pass" "$p3"; then
  echo "W2c_capability_core part 3: PASS ($(grep -c '^PASS ' "$p3") checks)"
else
  echo "W2c_capability_core part 3: FAIL (log $p3)"
  tail -12 "$p3"
  status=1
fi

# --- part 4: Django rows ------------------------------------------------------
echo "### W2c_capability_core part 4: Django rows (V4, the write-path rows)"
p4=$GATE_HOME/w2c-part4-django.log
if [ -z "${GATE_DJANGO_TEST:-}" ]; then
  echo "W2c_capability_core part 4: FAIL (GATE_DJANGO_TEST is not set)"
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
    echo "W2c_capability_core part 4: PASS ($(grep -Eo '[0-9]+ passed' "$p4" | tail -1))"
  else
    echo "W2c_capability_core part 4: FAIL (exit $rc; log $p4)"
    status=1
  fi
fi

# --- part 5: every earlier gate -------------------------------------------------
if [ "${GATE_SKIP_EARLIER:-}" = 1 ]; then
  echo "### W2c_capability_core part 5: skipped (GATE_SKIP_EARLIER=1; run the earlier gates separately)"
else
  echo "### W2c_capability_core part 5: W2b_mcp_seams.sh $BENCH_BASELINE (every earlier gate)"
  if GATE_HOME=$GATE_HOME bash "$GATES/W2b_mcp_seams.sh" "$BENCH_BASELINE"; then
    echo "W2c_capability_core part 5: PASS"
  else
    echo "W2c_capability_core part 5: FAIL"
    status=1
  fi
fi

if [ $status -eq 0 ]; then echo "W2c_capability_core: PASS"; else echo "W2c_capability_core: FAIL"; fi
exit $status
