#!/usr/bin/env bash
# Integration gate for the MCP seams.
#
# Proves, on this tree:
#   1. Named Go tests report --- PASS by name, so a renamed or skipped test
#      cannot pass by matching nothing: the MCP seam tests
#      (TestMCPHookOrderToolsCall ... TestMCPSecretsNotJSONMarshalled) and
#      TestClientFactoryDefault; the MCP, config, stdio, route and OCR
#      acceptance tests; the evidence, capabilities, calendar, credits,
#      thought-signature and MCP-auth rows; and the auth rows (every /v1 route
#      and every key-gated root requires a key on every spelling of its path,
#      and an admin key update keeps the key's own org). The MCP mock package
#      passes whole.
#   2. The move proof: go run ./scripts/movecheck on MOVE_COMMIT's internal/mcp
#      against PIN's prints an empty diff (the maintainer supplies both commits).
#   3. Scenarios: 62_mcpmock.sh, 63_stdio.sh, 03_unconfigured_routes.sh,
#      05_ocr.sh, and catalog_credits_live_rows.sh (a 410 model_expired with
#      no upstream call, the live credits body against credits-v1, and the
#      startup refusals of routing.scheduled.enabled and a FutureAGI rule with
#      no eval_id).
#   4. Django (GATE_DJANGO_TEST, a test-runner script): the MCP admin parser
#      accepts the mcp-admin-status-v1 and mcp-admin-tools-v1 goldens (both
#      PASSED, not skipped), and the MCP admin, contract, schema, config-push,
#      storage, retention, write-rule, reserved-key-metadata and key-org files
#      pass.
#   5. Migrations (GATE_DJANGO_MANAGE, a manage.py wrapper on the gate DB):
#      makemigrations prism usage --check --dry-run is clean, and prism
#      migrates down to 0034_request_outcome and back up to 0045.
#   6. Unless GATE_SKIP_EARLIER=1: every earlier gate (the twelve checks from
#      rollout_session.sh to content_mode_none.sh, the race run of
#      TestPolicyVersionStampedCandidateIsOneConfig, and call_order_seams.sh
#      against the benchmark baseline: the first argument, else PIN).
#
# Usage: mcp_seams.sh [bench-baseline]   (default: PIN; the maintainer supplies the commits PIN and MOVE_COMMIT)
# Environment: SCN_REDIS, SCN_REDIS_DB, PIN and MOVE_COMMIT (required); GATE_HOME;
# GATE_DJANGO_TEST and GATE_DJANGO_MANAGE (parts 4 and 5 fail without them); GATE_SKIP_EARLIER.
# Exit status: 0 when every part passes, 1 otherwise.
set -u

PIN=${PIN:-}
MOVE_COMMIT=${MOVE_COMMIT:-}
if [ -z "$PIN" ] || [ -z "$MOVE_COMMIT" ]; then
  echo "usage: mcp_seams.sh [bench-baseline] (set PIN and MOVE_COMMIT)" >&2
  exit 1
fi
# Part 2's move proof is against PIN; part 6's benchmark guard takes the
# baseline the calling gate names.
BENCH_PIN=${1:-$PIN}
GATES=$(cd "$(dirname "$0")" && pwd)
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
REPO=$(cd "$GW/.." && pwd)
GATE_HOME=${GATE_HOME:-${TMPDIR:-/tmp}/agentcc-gates}
SCN_TIMEOUT=${GATE_SCENARIO_TIMEOUT:-900}
mkdir -p "$GATE_HOME"

if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "mcp_seams: SCN_REDIS and a non-zero SCN_REDIS_DB are required" >&2
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
echo "### mcp_seams part 1: named Go tests (seams, acceptance, integration rows)"
SEAMS=(TestMCPHookOrderToolsCall TestMCPHookOrderToolsList TestMCPFinalizersDetachOnPark TestMCPRecordRegisteredBeforeTenant
  TestMCPRouteStubsRegistered TestMCPWiringOrder TestStateCodecRefusesMissingKey TestMCPSecretsNotJSONMarshalled TestClientFactoryDefault)
MCP_CORE_TESTS=(TestMCPFinalizersOnceOnPanicAndDenial TestMCPDepsLateBound TestMCPRawFieldsFilled TestTimeoutSkipsMCPPrefixes
  TestMCPStreamOutlivesDefaultTimeout TestMCPExtensionDepsDefaults TestMCPCallContextTermsAndEvidence TestRouteStubsRegistered TestWiringOrder)
MCP_CONFIG_TESTS=(TestStateCodecRoundTripTamperExpiryRotation TestSSEWriterKeepAliveAndDisconnect TestToolMarkupInheritsMarkup
  TestMCPConfigDefaultsAdditive TestMCPSecretsStillParseFromYAML TestForwardHeadersRefusedNamesAtLoad TestPrivateAllowRefusesMetadata
  TestFairUseDefaults TestMaxResultBytesDefault TestToolsKeysDefaults TestScheduledEnabledIsConfigError
  TestManagedServerKindBillingPriceCardShape TestRunToolAndSecurityDefaultsAdditive TestStateKeysFromEnvBeforeValidate TestFutureAGIRuleMissingConfigIsConfigError)
ORG_CONFIG_TESTS=(TestParseMCPv2 TestMCPUnknownKeyRejected TestMCPAccessorDefaults TestGoldenOrgConfigMCPv2 TestLegacyMCPAuthParses
  TestGoldenOrgConfigToolsV1 TestToolsAccessorDefaults)
STDIO_TESTS=(TestStdioChildSurvivesConnectCancel TestStdioReconnectSurvives TestStdioMinimalEnv TestStdioEnvFromHostAndValues
  TestStdioOversizeFailsPendingAndReconnects TestStdioConcurrentIdsNoCrossTalk TestStdioShutdownLadder TestSandboxLauncherUnavailable
  TestStdioStderrRedactedCappedDebug)
ROUTE_TESTS=(TestVideosUnconfigured501 TestVideosOrgScoped TestVideosForeignIdSame404 TestScheduledRoutesAll501 TestMissingConfigIsLoadError
  TestApiErrorStillFailsClosed TestMisconfiguredCheckFailsClosed)
OCR_TESTS=(TestOCRPagesListAndRangeForwardedVerbatim TestOCRRawBodyKeepsAnnotation TestOCRFileChunkAccepted TestVectorStoresNeverManaged
  TestOCRPricedPerPage TestOCRAnnotationPriced TestOCRByokMeteredFree TestOCRMetadataOnlyByDefault TestGeminiImageUsesGenerateContent
  TestShutdownModelRejected TestOpenAIImageDefaultNotDeprecated TestOCRAmbiguousFieldRefused TestDeprecationHostIsTheProviderType
  TestGeminiImageNOutOfRangeRefused TestGeminiImageErrorHidesAPIKey TestDeprecationHostAgreesWithVertexDetection)
INTEGRATION_ROWS=(TestLogEntryCarriesEvidenceSidecar TestLogEvidenceSnapshotAndRelease TestCapabilitiesSchemaAcceptsTheCatalogCreditsInstance
  TestCapabilitiesGolden TestCustomerCapabilitiesOmitResaleTerms TestCalendarShutdownIsModelExpired TestCalendarShutdownRefusedBeforeUpstream
  TestEmbeddedDeprecationsParse TestCreditsAdminCarriesOrgID TestCreditsGetKeepsBalanceUSD TestThoughtSignatureModelsUnsetUsesEngineRule
  TestThoughtSignatureModelsSetIsWired TestMCPEveryMethodWithoutAKeyIs401 TestRequestAPIKey TestServerShutdownClosesStdioChildrenInParallel
  TestOCRPagesMetadataKeyReserved TestMCPApprovalsDefaultTTLMustBePositive TestLoadCheckAndRuntimeCheckAgree)
AUTH_ROWS=(TestEveryV1RouteRequiresAKeyOnEverySpellingOfItsPath TestEveryV1RouteStillServesAnAuthenticatedCanonicalRequest
  TestRouterRejectsEveryNonCanonicalSpellingOfEveryRoute TestEveryNonPublicRouteOutsideV1RequiresAKeyOnEverySpelling
  TestKeyGatedRootsRefuseEveryMethodAndSubpathWithoutAKey TestKeyGatedRoutesServeAKeyHolder TestKeyAuthGatesEverySpellingOfAV1Path
  TestPerKeyToolFiltering_NoKeyIsRefused TestSSERequiresAKeyWhenKeyAuthIsSet
  TestUpdateKey_CannotRebindKeyToAnotherOrg TestUpdateKey_CannotSetAuthorityMetadata TestUpdateKey_KeepsItsOwnOrgAndLabels
  TestPlugin_KeyOrgIDIsTheAuthenticatedKeysOwn TestPlugin_ReservedMetadataIsNeverCopiedAsALabel TestAuthorityMetadataKeysAreReserved)
ALL=("${SEAMS[@]}" "${MCP_CORE_TESTS[@]}" "${MCP_CONFIG_TESTS[@]}" "${ORG_CONFIG_TESTS[@]}" "${STDIO_TESTS[@]}" "${ROUTE_TESTS[@]}" "${OCR_TESTS[@]}" "${INTEGRATION_ROWS[@]}" "${AUTH_ROWS[@]}")
p1=$GATE_HOME/mcp-seams-part1.log
(cd "$GW" && env -u AGENTCC_MCP_STATE_KEYS go test ./... -run "^($(join "${ALL[@]}"))\$" -count=1 -v &&
  go test ./cmd/agentcc-mcpmock/... -count=1) >"$p1" 2>&1
rc=$?
if [ $rc -eq 0 ] && expect_passed "$p1" "${ALL[@]}"; then
  echo "mcp_seams part 1: PASS (${#ALL[@]} named tests, plus the cmd/agentcc-mcpmock packages)"
else
  echo "mcp_seams part 1: FAIL (exit $rc, log $p1)"
  grep -E "^(--- FAIL|FAIL)" "$p1" | head -20
  status=1
fi

# --- part 2: the move proof ---
echo "### mcp_seams part 2: movecheck on the move commit against PIN"
mv_dir=$GATE_HOME/mcp-seams-movecheck
rm -rf "$mv_dir" && mkdir -p "$mv_dir/base" "$mv_dir/head"
(cd "$REPO" && git archive "$PIN" agentcc-gateway/internal/mcp | tar -x -C "$mv_dir/base" &&
  git archive "$MOVE_COMMIT" agentcc-gateway/internal/mcp | tar -x -C "$mv_dir/head")
out=$(cd "$GW" && go run ./scripts/movecheck --base "$mv_dir/base/agentcc-gateway/internal/mcp" --head "$mv_dir/head/agentcc-gateway/internal/mcp" 2>&1)
rc=$?
if [ $rc -eq 0 ] && [ -z "$out" ]; then
  echo "mcp_seams part 2: PASS (empty diff)"
else
  echo "mcp_seams part 2: FAIL (exit $rc): $out" | head -20
  status=1
fi

# --- part 3: scenarios -------------------------------------------------------
echo "### mcp_seams part 3: 62_mcpmock, 63_stdio, 03_unconfigured_routes, 05_ocr and catalog_credits_live_rows"
SCN_RUN=${SCN_RUN:-gate-catalog} SCN_HOME=${SCN_HOME:-$GATE_HOME/scn} \
  perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$SCN_TIMEOUT" \
  bash "$SC/run_all.sh" 62_mcpmock.sh 63_stdio.sh 03_unconfigured_routes.sh 05_ocr.sh
rc=$?
if [ $rc -eq 0 ]; then echo "mcp_seams part 3a: PASS"; else echo "mcp_seams part 3a: FAIL (exit $rc)"; status=1; fi
if GATE_HOME=$GATE_HOME bash "$GATES/catalog_credits_live_rows.sh"; then
  echo "mcp_seams part 3b: PASS"
else
  echo "mcp_seams part 3b: FAIL"
  status=1
fi

# --- part 4: Django rows ------------------------------------------------------
echo "### mcp_seams part 4: Django rows (MCP admin goldens, contracts, schemas, storage, retention, write rules, key org)"
p4=$GATE_HOME/mcp-seams-part4-django.log
if [ -z "${GATE_DJANGO_TEST:-}" ]; then
  echo "mcp_seams part 4: FAIL (GATE_DJANGO_TEST is not set)"
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
    echo "mcp_seams part 4: PASS ($(grep -Eo '[0-9]+ passed' "$p4" | tail -1); both golden tests PASSED, none skipped)"
  else
    echo "mcp_seams part 4: FAIL (exit $rc, goldens passed $goldens/2, log $p4)"
    grep -E "(FAILED|ERROR|SKIPPED)" "$p4" | head -20
    status=1
  fi
fi

# --- part 5: migrations -------------------------------------------------------
echo "### mcp_seams part 5: makemigrations --check and the prism round trip 0034 <-> 0045"
p5=$GATE_HOME/mcp-seams-part5-migrations.log
if [ -z "${GATE_DJANGO_MANAGE:-}" ]; then
  echo "mcp_seams part 5: FAIL (GATE_DJANGO_MANAGE is not set)"
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
    echo "mcp_seams part 5: PASS (no changes; prism 0045 -> 0034 -> 0045)"
  else
    echo "mcp_seams part 5: FAIL (exit $rc, log $p5)"
    tail -20 "$p5"
    status=1
  fi
fi

# --- part 6: every earlier gate -------------------------------------------------
if [ "${GATE_SKIP_EARLIER:-}" = 1 ]; then
  echo "### mcp_seams part 6: skipped (GATE_SKIP_EARLIER=1; run the earlier gates separately)"
else
  echo "### mcp_seams part 6: every earlier gate"
  for check in rollout_session catalog_pricing cache_tokens_priced strip_all priced_capabilities strategy_inputs \
    breaker_per_model candidate_managed_explicit endpoint_set_reserve direct_source_preferred degraded_excluded \
    content_mode_none; do
    if GATE_HOME=$GATE_HOME bash "$GATES/$check.sh" >"$GATE_HOME/$check.log" 2>&1; then
      echo "  $check: PASS ($(grep -Eo 'SCN PASS .*' "$GATE_HOME/$check.log" | tail -1))"
    else
      echo "  $check: FAIL (log $GATE_HOME/$check.log)"
      status=1
    fi
  done
  if (cd "$GW" && go test -race ./internal/server -run TestPolicyVersionStampedCandidateIsOneConfig -count=3) >"$GATE_HOME/race-policy.log" 2>&1; then
    echo "  race TestPolicyVersionStampedCandidateIsOneConfig x3: PASS"
  else
    echo "  race TestPolicyVersionStampedCandidateIsOneConfig x3: FAIL"
    status=1
  fi
  if GATE_HOME=$GATE_HOME bash "$GATES/call_order_seams.sh" "$BENCH_PIN"; then
    echo "mcp_seams part 6: call_order_seams PASS"
  else
    echo "mcp_seams part 6: call_order_seams FAIL"
    status=1
  fi
fi

if [ $status -eq 0 ]; then echo "mcp_seams: PASS"; else echo "mcp_seams: FAIL"; fi
exit $status
