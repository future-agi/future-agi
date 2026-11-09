#!/usr/bin/env bash
# W2b wave-gate integration check (plan section 3, integrator-owned).
#
# Proves, on the integrated W2b tree:
#   1. Named Go tests report --- PASS by name (duty 3), so a renamed or
#      skipped test cannot pass by matching nothing: the plan's MCP seam
#      tests (TestMCPHookOrderToolsCall ... TestMCPSecretsNotJSONMarshalled)
#      and M1's TestClientFactoryDefault (M1 meets M5 only through
#      NewClient); every W2b lane's named acceptance tests (M1, M1x, M2, M5,
#      V1, V3); and the integrator's rows: SR13
#      (TestLogEntryCarriesEvidenceSidecar), IC-2 (the SR1 instance and the
#      golden validate; G8's TestCapabilitiesGolden), IC-3 (the calendar's
#      model_expired), IC-4 (org_id in credits-v1), IC-5 (the thought
#      signature list), IC-6 (every /mcp method without a key is 401), and
#      the ported hotfixes' rows (authpath: every /v1 route on its //,
#      trailing-slash and dot-segment spellings is 401 and reaches no
#      handler, /mcp and /a2a are key-gated; keyorg: a key update that sets
#      org_id is refused by the gateway's admin PUT and the key keeps its own
#      org). M4's mock package passes whole.
#   2. M1's move proof: go run ./scripts/movecheck on M1's commit 1
#      (c4cf52f2de) against the PIN prints an empty diff.
#   3. Scenarios: 62_m4_mcpmock.sh, 63_m5_stdio.sh (now on M4's mock),
#      03_v1_unsafe_shells.sh, 05_v3_shell_hygiene.sh, and W2b_live_rows.sh
#      (IC-3's 410 model_expired with no upstream call, IC-4's live credits
#      body against credits-v1, M1x's startup refusals of
#      routing.scheduled.enabled and a FutureAGI rule with no eval_id).
#   4. Django (GATE_DJANGO_TEST, a lane-test script): M6's parser accepts M2's
#      mcp-admin-status-v1 and mcp-admin-tools-v1 goldens (both PASSED, not
#      skipped), M2's and M3's acceptance files, IC-1's storage test, IC-4's
#      retention ceiling, M2 IR-4's write rules, and keyorg's Django rows
#      (a serializer refuses org_id and every reserved name; the reserved list
#      equals the gateway's).
#   5. Migrations (GATE_DJANGO_MANAGE, a manage.py wrapper on the gate DB):
#      makemigrations prism usage --check --dry-run is clean, and prism
#      migrates down to 0034_request_outcome and back up to 0045 (M3's round
#      trip, extended by IC-1).
#   6. Unless GATE_SKIP_EARLIER=1: every earlier gate (W0b_seams.sh against
#      the benchmark baseline, the first argument, else the PIN 1c50ff5536,
#      with allocs budget 0, the seven W1 and five W2 checks, and the W2 race
#      run of TestPolicyVersionStampedCandidateIsOneConfig). The W2c gate
#      passes the merged W2b tree 2786e6c7ff (C221).
#
# Usage: W2b_mcp_seams.sh [bench-baseline]
# Environment: SCN_REDIS, SCN_REDIS_DB (required); GATE_HOME; GATE_DJANGO_TEST
# and GATE_DJANGO_MANAGE (parts 4 and 5 fail without them); GATE_SKIP_EARLIER.
# Exit status: 0 when every part passes, 1 otherwise.
set -u

PIN=1c50ff55365ba738e434283b0d4a4d3d951ebdcd
# Part 2's move proof is against the W2b base PIN; part 6's benchmark guard
# takes the baseline the calling gate names (W2c integration, C221).
BENCH_PIN=${1:-$PIN}
M1_COMMIT1=c4cf52f2de
GATES=$(cd "$(dirname "$0")" && pwd)
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
REPO=$(cd "$GW/.." && pwd)
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
SCN_TIMEOUT=${GATE_SCENARIO_TIMEOUT:-900}
mkdir -p "$GATE_HOME"

if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "W2b_mcp_seams: SCN_REDIS and a non-zero SCN_REDIS_DB are required" >&2
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
echo "### W2b_mcp_seams part 1: named Go tests (seams, lane acceptance, integrator rows)"
SEAMS=(TestMCPHookOrderToolsCall TestMCPHookOrderToolsList TestMCPFinalizersDetachOnPark TestMCPRecordRegisteredBeforeTenant
  TestMCPRouteStubsRegistered TestMCPWiringOrder TestStateCodecRefusesMissingKey TestMCPSecretsNotJSONMarshalled TestClientFactoryDefault)
M1=(TestMCPFinalizersOnceOnPanicAndDenial TestMCPDepsLateBound TestMCPRawFieldsFilled TestTimeoutSkipsMCPPrefixes
  TestMCPStreamOutlivesDefaultTimeout TestMCPSpliceDepsDefaults TestMCPCallContextTermsAndEvidence TestRouteStubsRegistered TestWiringOrder)
M1X=(TestStateCodecRoundTripTamperExpiryRotation TestSSEWriterKeepAliveAndDisconnect TestToolMarkupInheritsMarkup
  TestMCPConfigDefaultsAdditive TestMCPSecretsStillParseFromYAML TestForwardHeadersRefusedNamesAtLoad TestPrivateAllowRefusesMetadata
  TestFairUseDefaults TestMaxResultBytesDefault TestToolsKeysDefaults TestScheduledEnabledIsConfigError
  TestManagedServerKindBillingPriceCardShape TestPKeysDefaultsAdditive TestStateKeysFromEnvBeforeValidate TestFutureAGIRuleMissingConfigIsConfigError)
M2=(TestParseMCPv2 TestMCPUnknownKeyRejected TestMCPAccessorDefaults TestGoldenOrgConfigMCPv2 TestLegacyMCPAuthStillParses
  TestGoldenOrgConfigToolsV1 TestToolsAccessorDefaults)
M5=(TestStdioChildSurvivesConnectCancel TestStdioReconnectSurvives TestStdioMinimalEnv TestStdioEnvFromHostAndValues
  TestStdioOversizeFailsPendingAndReconnects TestStdioConcurrentIdsNoCrossTalk TestStdioShutdownLadder TestSandboxLauncherUnavailable
  TestStdioStderrRedactedCappedDebug)
V1=(TestVideosUnconfigured501 TestVideosOrgScoped TestVideosForeignIdSame404 TestScheduledRoutesAll501 TestMissingConfigIsLoadError
  TestApiErrorStillFailsClosed TestMisconfiguredCheckFailsClosed)
V3=(TestOCRPagesListAndRangeForwardedVerbatim TestOCRRawBodyKeepsAnnotation TestOCRFileChunkAccepted TestVectorStoresNeverManaged
  TestOCRPricedPerPage TestOCRAnnotationPriced TestOCRByokMeteredFree TestOCRMetadataOnlyByDefault TestGeminiImageUsesGenerateContent
  TestShutdownModelRejected TestOpenAIImageDefaultNotDeprecated TestOCRAmbiguousFieldRefused TestDeprecationHostIsTheProviderType
  TestGeminiImageNOutOfRangeRefused TestGeminiImageErrorHidesAPIKey TestDeprecationHostAgreesWithVertexDetection)
INTEGRATOR=(TestLogEntryCarriesEvidenceSidecar TestLogEvidenceSnapshotAndRelease TestCapabilitiesSchemaAcceptsSR1Instance
  TestCapabilitiesGolden TestCustomerCapabilitiesOmitResaleTerms TestCalendarShutdownIsModelExpired TestCalendarShutdownRefusedBeforeUpstream
  TestEmbeddedDeprecationsParse TestCreditsAdminCarriesOrgID TestCreditsGetKeepsBalanceUSD TestThoughtSignatureModelsUnsetUsesEngineRule
  TestThoughtSignatureModelsSetIsWired TestMCPEveryMethodWithoutAKeyIs401 TestRequestAPIKey TestServerShutdownClosesStdioChildrenInParallel
  TestOCRPagesMetadataKeyReserved TestMCPApprovalsDefaultTTLMustBePositive TestLoadCheckAndRuntimeCheckAgree)
HOTFIX=(TestEveryV1RouteRequiresAKeyOnEverySpellingOfItsPath TestEveryV1RouteStillServesAnAuthenticatedCanonicalRequest
  TestRouterRejectsEveryNonCanonicalSpellingOfEveryRoute TestEveryNonPublicRouteOutsideV1RequiresAKeyOnEverySpelling
  TestKeyGatedRootsRefuseEveryMethodAndSubpathWithoutAKey TestKeyGatedRoutesServeAKeyHolder TestKeyAuthGatesEverySpellingOfAV1Path
  TestPerKeyToolFiltering_NoKeyIsRefused TestSSERequiresAKeyWhenKeyAuthIsSet
  TestUpdateKey_CannotRebindKeyToAnotherOrg TestUpdateKey_CannotSetAuthorityMetadata TestUpdateKey_KeepsItsOwnOrgAndLabels
  TestPlugin_KeyOrgIDIsTheAuthenticatedKeysOwn TestPlugin_ReservedMetadataIsNeverCopiedAsALabel TestAuthorityMetadataKeysAreReserved)
ALL=("${SEAMS[@]}" "${M1[@]}" "${M1X[@]}" "${M2[@]}" "${M5[@]}" "${V1[@]}" "${V3[@]}" "${INTEGRATOR[@]}" "${HOTFIX[@]}")
p1=$GATE_HOME/w2b-part1.log
(cd "$GW" && env -u AGENTCC_MCP_STATE_KEYS go test ./... -run "^($(join "${ALL[@]}"))\$" -count=1 -v &&
  go test ./cmd/agentcc-mcpmock/... -count=1) >"$p1" 2>&1
rc=$?
if [ $rc -eq 0 ] && expect_passed "$p1" "${ALL[@]}"; then
  echo "W2b_mcp_seams part 1: PASS (${#ALL[@]} named tests, plus M4's cmd/agentcc-mcpmock packages)"
else
  echo "W2b_mcp_seams part 1: FAIL (exit $rc, log $p1)"
  grep -E "^(--- FAIL|FAIL)" "$p1" | head -20
  status=1
fi

# --- part 2: M1's move proof ------------------------------------------------
echo "### W2b_mcp_seams part 2: movecheck on M1's commit 1 against the PIN"
mv_dir=$GATE_HOME/w2b-movecheck
rm -rf "$mv_dir" && mkdir -p "$mv_dir/base" "$mv_dir/head"
(cd "$REPO" && git archive "$PIN" agentcc-gateway/internal/mcp | tar -x -C "$mv_dir/base" &&
  git archive "$M1_COMMIT1" agentcc-gateway/internal/mcp | tar -x -C "$mv_dir/head")
out=$(cd "$GW" && go run ./scripts/movecheck --base "$mv_dir/base/agentcc-gateway/internal/mcp" --head "$mv_dir/head/agentcc-gateway/internal/mcp" 2>&1)
rc=$?
if [ $rc -eq 0 ] && [ -z "$out" ]; then
  echo "W2b_mcp_seams part 2: PASS (empty diff)"
else
  echo "W2b_mcp_seams part 2: FAIL (exit $rc): $out" | head -20
  status=1
fi

# --- part 3: scenarios -------------------------------------------------------
echo "### W2b_mcp_seams part 3: 62_m4, 63_m5, 03_v1, 05_v3 and W2b_live_rows"
SCN_LANE=${SCN_LANE:-x6-w2b-gate} SCN_HOME=${SCN_HOME:-$GATE_HOME/scn} \
  perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$SCN_TIMEOUT" \
  bash "$SC/run_all.sh" 62_m4_mcpmock.sh 63_m5_stdio.sh 03_v1_unsafe_shells.sh 05_v3_shell_hygiene.sh
rc=$?
if [ $rc -eq 0 ]; then echo "W2b_mcp_seams part 3a: PASS"; else echo "W2b_mcp_seams part 3a: FAIL (exit $rc)"; status=1; fi
if GATE_HOME=$GATE_HOME bash "$GATES/W2b_live_rows.sh"; then
  echo "W2b_mcp_seams part 3b: PASS"
else
  echo "W2b_mcp_seams part 3b: FAIL"
  status=1
fi

# --- part 4: Django rows ------------------------------------------------------
echo "### W2b_mcp_seams part 4: Django rows (M6 goldens, M2, M3, IC-1, IC-4, M2 IR-4, keyorg)"
p4=$GATE_HOME/w2b-part4-django.log
if [ -z "${GATE_DJANGO_TEST:-}" ]; then
  echo "W2b_mcp_seams part 4: FAIL (GATE_DJANGO_TEST is not set)"
  status=1
else
  "$GATE_DJANGO_TEST" agentcc/tests/test_mcp_admin_api.py agentcc/tests/test_contract_mcp.py agentcc/tests/test_contract_v2.py \
    agentcc/tests/test_config_push_optin_and_validation.py agentcc/tests/test_schema_mcp.py agentcc/tests/test_schema_v2.py \
    agentcc/tests/test_gateway_mcp_and_analytics_guardrail_api.py agentcc/tests/test_org_config_tools_identity_storage.py \
    agentcc/tests/test_retention_ceiling.py agentcc/tests/test_org_config_mcp_write_rules.py \
    agentcc/tests/test_reserved_key_metadata_parity.py agentcc/tests/test_check_key_org_command.py \
    "agentcc/tests/test_api.py::TestAgentccAPIKeyReservedMetadata" "agentcc/tests/test_api.py::TestAgentccAPIKeyDelete" \
    -v -p no:cacheprovider >"$p4" 2>&1
  rc=$?
  goldens=0
  for t in test_parser_accepts_mcp_admin_status_v1_golden test_parser_accepts_mcp_admin_tools_v1_golden; do
    grep -Eq "::${t} PASSED" "$p4" && goldens=$((goldens + 1))
  done
  if [ $rc -eq 0 ] && [ $goldens -eq 2 ] && ! grep -q " SKIPPED" "$p4"; then
    echo "W2b_mcp_seams part 4: PASS ($(grep -Eo '[0-9]+ passed' "$p4" | tail -1); both M6 golden tests PASSED, none skipped)"
  else
    echo "W2b_mcp_seams part 4: FAIL (exit $rc, goldens passed $goldens/2, log $p4)"
    grep -E "(FAILED|ERROR|SKIPPED)" "$p4" | head -20
    status=1
  fi
fi

# --- part 5: migrations -------------------------------------------------------
echo "### W2b_mcp_seams part 5: makemigrations --check and the prism round trip 0034 <-> 0045"
p5=$GATE_HOME/w2b-part5-migrations.log
if [ -z "${GATE_DJANGO_MANAGE:-}" ]; then
  echo "W2b_mcp_seams part 5: FAIL (GATE_DJANGO_MANAGE is not set)"
  status=1
else
  # Startup refuses migrate and makemigrations unless DB mutations are allowed.
  export NO_STARTUP_DB_MUTATIONS=false
  {
    "$GATE_DJANGO_MANAGE" makemigrations prism usage --check --dry-run &&
      "$GATE_DJANGO_MANAGE" migrate --noinput &&
      "$GATE_DJANGO_MANAGE" migrate prism 0034_request_outcome --noinput &&
      "$GATE_DJANGO_MANAGE" migrate prism --noinput &&
      "$GATE_DJANGO_MANAGE" showmigrations prism
  } >"$p5" 2>&1
  rc=$?
  if [ $rc -eq 0 ] && grep -q "No changes detected" "$p5" && grep -q "Unapplying prism.0045_org_config_tools_identity" "$p5" &&
    grep -q "Applying prism.0045_org_config_tools_identity" "$p5" && grep -q "\[X\] 0045_org_config_tools_identity" "$p5"; then
    echo "W2b_mcp_seams part 5: PASS (no changes; prism 0045 -> 0034 -> 0045)"
  else
    echo "W2b_mcp_seams part 5: FAIL (exit $rc, log $p5)"
    tail -20 "$p5"
    status=1
  fi
fi

# --- part 6: every earlier gate -------------------------------------------------
if [ "${GATE_SKIP_EARLIER:-}" = 1 ]; then
  echo "### W2b_mcp_seams part 6: skipped (GATE_SKIP_EARLIER=1; run the earlier gates separately)"
else
  echo "### W2b_mcp_seams part 6: every earlier gate"
  for check in W1_rollout_session W1_catalog_pricing W1_cache_tokens_priced W1_strip_all W1_priced_capabilities W1_strategy_inputs \
    W1_breaker_per_model W2_candidate_managed_explicit W2_endpoint_set_reserve W2_direct_source_preferred W2_degraded_excluded \
    W2_content_mode_none; do
    if GATE_HOME=$GATE_HOME bash "$GATES/$check.sh" >"$GATE_HOME/$check.log" 2>&1; then
      echo "  $check: PASS ($(grep -Eo 'SCN PASS .*' "$GATE_HOME/$check.log" | tail -1))"
    else
      echo "  $check: FAIL (log $GATE_HOME/$check.log)"
      status=1
    fi
  done
  if (cd "$GW" && go test -race ./internal/server -run TestPolicyVersionStampedCandidateIsOneConfig -count=3) >"$GATE_HOME/w2-race-policy.log" 2>&1; then
    echo "  W2 race TestPolicyVersionStampedCandidateIsOneConfig x3: PASS"
  else
    echo "  W2 race TestPolicyVersionStampedCandidateIsOneConfig x3: FAIL"
    status=1
  fi
  if GATE_HOME=$GATE_HOME bash "$GATES/W0b_seams.sh" "$BENCH_PIN"; then
    echo "W2b_mcp_seams part 6: W0b_seams PASS"
  else
    echo "W2b_mcp_seams part 6: W0b_seams FAIL"
    status=1
  fi
fi

if [ $status -eq 0 ]; then echo "W2b_mcp_seams: PASS"; else echo "W2b_mcp_seams: FAIL"; fi
exit $status
