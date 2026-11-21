import { mapChecks, mapCompare, mapDraft, mapSkill } from './mappers';

// Only rename declared structural fields. Prompts, JSON assertions, schemas,
// provider messages and user-defined object keys must remain byte-for-byte data.
const fields = (value, names) => Object.fromEntries(names.map(([wire, view = wire]) => [view, value[wire]]));
const nullable = (mapper, value) => value == null ? value : mapper(value);
export const mapV11Page = (value, mapper) => ({
  results: value.results.map(mapper), count: value.count, page: value.page,
  pageSize: value.page_size, totalPages: value.total_pages,
});
export const mapModelConfig = (value) => ({ modelId: value.model_id, temperature: value.temperature, maxOutputTokens: value.max_output_tokens });
export const modelConfigInput = (value) => ({ model_id: value.modelId, temperature: value.temperature, max_output_tokens: value.maxOutputTokens });
export const mapSnapshotIdentity = (value) => fields(value, [['skill_id', 'skillId'], ['version_id', 'versionId'], ['version'], ['draft_revision', 'draftRevision'], ['digest'], ['execution_manifest_digest', 'executionManifestDigest']]);
export const mapCurrentness = (value) => fields(value, [['content'], ['baseline'], ['suite'], ['competitors'], ['payload_available', 'payloadAvailable']]);
export const mapUsage = (value) => fields(value, [['known_input_tokens', 'knownInputTokens'], ['known_output_tokens', 'knownOutputTokens'], ['known_total_tokens', 'knownTotalTokens'], ['unknown_calls', 'unknownCalls'], ['complete']]);
export const mapCost = (value) => fields(value, [['known_estimate_usd', 'knownEstimateUsd'], ['reserved_estimate_usd', 'reservedEstimateUsd'], ['unknown_calls', 'unknownCalls'], ['complete'], ['pricing_source', 'pricingSource']]);
export const mapMetrics = (value) => ({
  taskUsage: mapUsage(value.task_usage), judgeUsage: mapUsage(value.judge_usage),
  taskCost: mapCost(value.task_cost), judgeCost: mapCost(value.judge_cost),
  elapsedMs: value.elapsed_ms, providerDurationMs: value.provider_duration_ms,
});
export const mapCase = (value) => ({
  ...fields(value, [['id'], ['title'], ['source_group_id', 'sourceGroupId'], ['prompt'], ['fixtures'], ['expected_trigger', 'expectedTrigger'], ['reviewed'], ['reviewed_by', 'reviewedBy'], ['reviewed_at', 'reviewedAt'], ['origin'], ['exposed_to_assist', 'exposedToAssist']]),
  checks: value.checks.map((check) => ({ ...check, config: check.kind === 'contains' || check.kind === 'not_contains' ? { text: check.config.text, caseSensitive: check.config.case_sensitive } : check.config })),
});
export const caseInput = (value) => ({
  id: value.id, title: value.title, source_group_id: value.sourceGroupId, prompt: value.prompt,
  fixtures: value.fixtures.map(({ id, name, text }) => ({ id, name, text })), expected_trigger: value.expectedTrigger,
  checks: value.checks.map(({ id, kind, critical, config }) => ({ id, kind, critical, config: kind === 'contains' || kind === 'not_contains' ? { text: config.text, case_sensitive: config.caseSensitive } : config })),
  reviewed: value.reviewed,
});
export const candidateInput = (value) => value.kind === 'draft'
  ? { kind: value.kind, revision: value.revision, digest: value.digest }
  : { kind: value.kind, version: value.version, digest: value.digest };
export const runConfigInput = (value) => ({
  candidate: candidateInput(value.candidate),
  cases: value.cases.kind === 'saved'
    ? { kind: 'saved', suite_revision: value.cases.suiteRevision, case_ids: value.cases.caseIds }
    : { kind: 'ad_hoc', cases: value.cases.cases.map(caseInput) },
  baselines: value.baselines, competitors: value.competitors.map(({ skillId, version }) => ({ skill_id: skillId, version })),
  model: modelConfigInput(value.model), judge: nullable(modelConfigInput, value.judge),
  profile: value.profile, repetitions: value.repetitions,
  limits: { max_total_tokens: value.limits.maxTotalTokens, max_estimated_cost_usd: value.limits.maxEstimatedCostUsd },
  purpose: value.purpose, parent_run_id: value.parentRunId,
});
export const inventoryReviewInput = (value) => ({
  fingerprint: value.fingerprint, reviewed_all_files: value.reviewedAllFiles, no_known_answer_keys: value.noKnownAnswerKeys,
  classifications: value.classifications.map(({ skillId, path, sha256, classification, reason }) => ({ skill_id: skillId, path, sha256, classification, reason })),
  false_positive_secret_findings: value.falsePositiveSecretFindings.map(({ findingId, reason }) => ({ finding_id: findingId, reason })),
});
export const mapProfileLimits = (value) => fields(value, [
  ['max_cases', 'maxCases'], ['max_variants', 'maxVariants'], ['repetitions'], ['max_attempts', 'maxAttempts'],
  ['deadline_seconds', 'deadlineSeconds'], ['max_task_turns_per_attempt', 'maxTaskTurnsPerAttempt'],
  ['max_tool_calls_per_attempt', 'maxToolCallsPerAttempt'], ['task_phase_seconds', 'taskPhaseSeconds'],
  ['judge_phase_seconds', 'judgePhaseSeconds'], ['provider_call_seconds', 'providerCallSeconds'],
  ['max_task_output_tokens', 'maxTaskOutputTokens'], ['max_judge_output_tokens', 'maxJudgeOutputTokens'],
  ['max_delivered_bytes_per_attempt', 'maxDeliveredBytesPerAttempt'], ['max_total_tokens', 'maxTotalTokens'],
]);
export const mapLimits = (value) => ({
  ...fields(value, [['max_competitors', 'maxCompetitors'], ['max_active_attempts_per_run', 'maxActiveAttemptsPerRun'], ['max_active_provider_calls_per_organization', 'maxActiveProviderCallsPerOrganization'], ['max_active_model_jobs_per_organization', 'maxActiveModelJobsPerOrganization'], ['max_queue_wait_seconds', 'maxQueueWaitSeconds'], ['max_estimated_cost_usd', 'maxEstimatedCostUsd']]),
  profiles: { quick: mapProfileLimits(value.profiles.quick), repeated: mapProfileLimits(value.profiles.repeated) },
});
export const mapOutboundFinding = (value) => ({
  findingId: value.finding_id, code: value.code, explanation: value.explanation,
  location: fields(value.location, [['kind'], ['skill_id', 'skillId'], ['digest'], ['path'], ['case_id', 'caseId'], ['fixture_id', 'fixtureId'], ['check_id', 'checkId'], ['pointer'], ['line']]),
});
export const mapTestingOptions = (value) => ({
  enabled: value.enabled, assistEnabled: value.assist_enabled === true, githubEnabled: value.github_enabled, limits: mapLimits(value.limits), harnessVersion: value.harness_version, evaluatorVersion: value.evaluator_version,
  models: value.models.map((model) => fields(model, [['model_id', 'modelId'], ['provider'], ['configured'], ['tool_support', 'toolSupport'], ['context_limit', 'contextLimit'], ['pricing_status', 'pricingStatus'], ['can_run', 'canRun'], ['disabled_reason', 'disabledReason']])),
});
export const mapRunPreview = (value) => ({
  ...fields(value, [['fingerprint'], ['production_event_id', 'productionEventId'], ['release_revision', 'releaseRevision'], ['case_count', 'caseCount'], ['variant_count', 'variantCount'], ['repetitions'], ['attempt_count', 'attemptCount'], ['max_task_calls', 'maxTaskCalls'], ['max_judge_calls', 'maxJudgeCalls'], ['warnings']]),
  candidate: mapSnapshotIdentity(value.candidate), production: nullable(mapSnapshotIdentity, value.production), competitors: value.competitors.map(mapSnapshotIdentity),
  estimate: { upperBoundUsd: value.estimate.upper_bound_usd, pricingSource: value.estimate.pricing_source }, limits: mapLimits(value.limits),
  inventory: value.inventory.map((bundle) => ({ skillId: bundle.skill_id, digest: bundle.digest, files: bundle.files.map((file) => ({ path: file.path, sha256: file.sha256, sizeBytes: file.size_bytes, flags: file.flags })) })),
  outboundFindings: value.outbound_findings.map(mapOutboundFinding),
  providerDisclosure: { taskProvider: value.provider_disclosure.task_provider, judgeProvider: value.provider_disclosure.judge_provider },
});
export const assistInput = (value) => ({
  kind: value.kind, model: modelConfigInput(value.model), max_estimated_cost_usd: value.maxEstimatedCostUsd,
  ...(value.kind === 'summary' ? { from_version: value.fromVersion, to: candidateInput(value.to) } : { revision: value.revision, expected_digest: value.expectedDigest }),
  ...(value.kind === 'draft' ? { instruction: value.instruction, context_text: value.contextText } : {}),
  ...(value.kind === 'description' ? { source_run_id: value.sourceRunId, case_ids: value.caseIds } : {}),
});
export const mapAssistPreview = (value) => ({
  inputFingerprint: value.input_fingerprint,
  providerDisclosure: { provider: value.provider_disclosure.provider },
  allowance: fields(value.allowance, [['input_bytes', 'inputBytes'], ['max_input_bytes', 'maxInputBytes'], ['max_total_tokens', 'maxTotalTokens'], ['max_output_tokens', 'maxOutputTokens'], ['max_output_bytes', 'maxOutputBytes'], ['max_files', 'maxFiles'], ['deadline_seconds', 'deadlineSeconds']]),
  estimate: { upperBoundUsd: value.estimate.upper_bound_usd, pricingSource: value.estimate.pricing_source },
  outboundFindings: value.outbound_findings.map(mapOutboundFinding),
});
export const mapSuite = (value) => ({
  revision: value.revision, digest: value.digest, cases: value.cases.map(mapCase),
  updatedAt: value.updated_at, updatedBy: value.updated_by, visibility: value.visibility,
});
export const mapJobAck = (value) => ({ id: value.id, status: value.status, pollAfterMs: value.poll_after_ms, deadlineAt: value.deadline_at });
export const mapRunAck = (value) => ({ ...mapJobAck(value), visibility: value.visibility, attemptCount: value.attempt_count, executionFingerprint: value.execution_fingerprint });
export const mapExecutionConfiguration = (value) => ({
  model: mapModelConfig(value.model), judge: nullable(mapModelConfig, value.judge),
  harnessVersion: value.harness_version, adapterVersion: value.adapter_version, evaluatorVersion: value.evaluator_version,
});
export const mapRun = (value) => ({
  ...fields(value, [['id'], ['status'], ['profile'], ['visibility'], ['created_at', 'createdAt'], ['started_at', 'startedAt'], ['finished_at', 'finishedAt'], ['deadline_at', 'deadlineAt'], ['parent_run_id', 'parentRunId'], ['execution_fingerprint', 'executionFingerprint'], ['suite_revision', 'suiteRevision'], ['selected_case_ids', 'selectedCaseIds'], ['full_suite', 'fullSuite'], ['repetitions'], ['validity'], ['validity_reasons', 'validityReasons'], ['error'], ['payload_expires_at', 'payloadExpiresAt'], ['payload_expired', 'payloadExpired']]),
  candidate: mapSnapshotIdentity(value.candidate), production: nullable(mapSnapshotIdentity, value.production), competitors: value.competitors.map(mapSnapshotIdentity),
  executionConfiguration: mapExecutionConfiguration(value.execution_configuration), currentness: mapCurrentness(value.currentness), metrics: mapMetrics(value.metrics),
  counts: fields(value.counts, [['planned'], ['finished'], ['answered'], ['graded'], ['errors'], ['incomplete'], ['not_started', 'notStarted']]),
  decision: fields(value.decision, [['state'], ['improved_cases', 'improvedCases'], ['regressed_cases', 'regressedCases'], ['unchanged_cases', 'unchangedCases'], ['unmatched_cases', 'unmatchedCases']]),
  answerComparisons: value.answer_comparisons.map((row) => fields(row, [['baseline'], ['matched_cases', 'matchedCases'], ['expected_cases', 'expectedCases'], ['delta_percentage_points', 'deltaPercentagePoints']])),
});
export const mapCaseResult = (value) => ({
  caseId: value.case_id, title: value.title, expectedTrigger: value.expected_trigger, change: value.change,
  answerComparisons: value.answer_comparisons.map((row) => fields(row, [['baseline'], ['matched_cases', 'matchedCases'], ['expected_cases', 'expectedCases'], ['delta_percentage_points', 'deltaPercentagePoints']])),
  variants: value.variants.map((variant) => ({
    ...fields(variant, [['variant'], ['planned'], ['completed'], ['loaded'], ['answer_passed', 'answerPassed'], ['answer_graded', 'answerGraded'], ['delivery_passed', 'deliveryPassed'], ['inconsistent']]),
    attempts: variant.attempts.map((attempt) => fields(attempt, [['id'], ['repeat_index', 'repeatIndex'], ['status'], ['routing_verdict', 'routingVerdict'], ['delivery_verdict', 'deliveryVerdict'], ['answer_verdict', 'answerVerdict']])),
  })),
});
export const mapCall = (value) => ({
  ...fields(value, [['id'], ['sequence'], ['kind'], ['state'], ['assistant_message', 'assistantMessage'], ['finish_reason', 'finishReason'], ['provider_request_id', 'providerRequestId'], ['requested_model', 'requestedModel'], ['returned_model', 'returnedModel'], ['latency_ms', 'latencyMs'], ['error']]),
  usage: mapUsage(value.usage), cost: mapCost(value.cost),
});
export const mapReceipt = (value) => fields(value, [['call_id', 'callId'], ['tool_call_id', 'toolCallId'], ['skill_id', 'skillId'], ['version_id', 'versionId'], ['digest'], ['path'], ['sha256'], ['offset'], ['next_offset', 'nextOffset'], ['total_chars', 'totalChars'], ['eof'], ['delivered_bytes', 'deliveredBytes'], ['outcome'], ['error_code', 'errorCode']]);
export const mapAttempt = (value) => ({
  ...fields(value, [['id'], ['variant'], ['repeat_index', 'repeatIndex'], ['status'], ['answer'], ['target_requested', 'targetRequested'], ['target_loaded', 'targetLoaded'], ['root_fully_delivered', 'rootFullyDelivered'], ['routing_verdict', 'routingVerdict'], ['delivery_verdict', 'deliveryVerdict'], ['answer_verdict', 'answerVerdict'], ['error']]),
  case: mapCase(value.case), receipts: value.receipts.map(mapReceipt), calls: value.calls.map(mapCall), metrics: mapMetrics(value.metrics),
  checks: value.checks.map((check) => ({ ...fields(check, [['check_id', 'checkId'], ['category'], ['verdict'], ['expected'], ['actual'], ['evidence'], ['reason']]) })),
});
export const mapRunFile = (value) => fields(value, [['skill_id', 'skillId'], ['version_id', 'versionId'], ['digest'], ['path'], ['sha256'], ['size_bytes', 'sizeBytes'], ['total_chars', 'totalChars'], ['offset'], ['next_offset', 'nextOffset'], ['eof'], ['text']]);
export const mapEvidence = (value) => ({
  candidateDigest: value.candidate_digest, currentReleaseRevision: value.current_release_revision, reasonIfNone: value.reason_if_none,
  runs: value.runs.map((run) => ({
    ...fields(run, [['run_id', 'runId'], ['status'], ['visibility'], ['validity'], ['selected_cases', 'selectedCases'], ['suite_cases', 'suiteCases'], ['repetitions'], ['answer_regressions', 'answerRegressions'], ['routing_failures', 'routingFailures'], ['delivery_failures', 'deliveryFailures'], ['error_attempts', 'errorAttempts'], ['created_at', 'createdAt'], ['can_attach', 'canAttach']]),
    currentness: mapCurrentness(run.currentness),
  })),
});
export const mapProtection = (value) => ({ protectedLabelNames: value.protected_label_names, releaseRevision: value.release_revision, canManage: value.can_manage });
export const mapProtectionEvent = (value) => fields(value, [['old_names', 'oldNames'], ['new_names', 'newNames'], ['from_revision', 'fromRevision'], ['to_revision', 'toRevision'], ['actor'], ['created_at', 'createdAt']]);
export const mapAdoption = (value) => ({
  ...mapV11Page(value, (row) => ({
    ...fields(row, [['consumer_id', 'consumerId'], ['consumer_label', 'consumerLabel'], ['channel'], ['state'], ['last_at', 'lastAt']]),
    versions: row.versions.map((version) => fields(version, [['version'], ['version_id', 'versionId'], ['last_at', 'lastAt']])),
  })),
  target: nullable((target) => fields(target, [['label'], ['version'], ['version_id', 'versionId'], ['promotion_event_id', 'promotionEventId'], ['promoted_at', 'promotedAt']]), value.target),
  window: { start: value.window.start, end: value.window.end, asOf: value.window.as_of }, coverage: value.coverage, historicalTimestampQuality: value.historical_timestamp_quality,
  summary: fields(value.summary, [['current_only', 'currentOnly'], ['other_only', 'otherOnly'], ['mixed'], ['not_seen', 'notSeen'], ['ambiguous']]),
});
export const mapSource = (value) => fields(value, [['provider'], ['revision'], ['repo'], ['repository_id', 'repositoryId'], ['path'], ['ref'], ['ref_kind', 'refKind'], ['imported_sha', 'importedSha'], ['upstream_digest', 'upstreamDigest'], ['associated_version', 'associatedVersion'], ['checked_sha', 'checkedSha'], ['checked_at', 'checkedAt'], ['check_status', 'checkStatus']]);
export const mapGitHubPreview = (value) => ({
  ...fields(value, [['id'], ['status'], ['operation'], ['expires_at', 'expiresAt'], ['outcome'], ['roots'], ['local_diverged', 'localDiverged'], ['source_revision', 'sourceRevision'], ['expected_latest_version', 'expectedLatestVersion'], ['expected_latest_digest', 'expectedLatestDigest'], ['can_stage', 'canStage'], ['error'], ['retry_at', 'retryAt']]),
  source: fields(value.source, [['repo'], ['repository_id', 'repositoryId'], ['path'], ['ref'], ['ref_kind', 'refKind'], ['commit_sha', 'commitSha']]),
  bundle: nullable((bundle) => ({ name: bundle.name, digest: bundle.digest, files: bundle.files.map((file) => ({ path: file.path, sha256: file.sha256, sizeBytes: file.size_bytes })), checks: mapChecks(bundle.checks) }), value.bundle),
  license: { status: value.license.status, declared: value.license.declared, requiresAdminReview: value.license.requires_admin_review, files: value.license.files.map((file) => ({ path: file.path, sha256: file.sha256, urlAtCommit: file.url_at_commit, text: file.text })) },
  diff: nullable(mapCompare, value.diff), consumed: nullable((receipt) => ({ skillId: receipt.skill_id, draftRevision: receipt.draft_revision }), value.consumed),
});
export const mapSourceDraft = (value) => value == null ? value : { ...mapDraft(value), source: value.source };
export const mapStagedSource = (value) => ({ draft: mapSourceDraft(value.draft), published: value.published, consumed: value.consumed ? { skillId: value.consumed.skill_id, draftRevision: value.consumed.draft_revision } : null });
export const mapStagedImport = (value) => ({ ...mapStagedSource(value), skill: mapSkill(value.skill) });
export const mapAssistAck = (value) => ({ ...mapJobAck(value), kind: value.kind, baseRevision: value.base_revision, baseDigest: value.base_digest });
export const mapAssist = (value) => ({
  ...fields(value, [['id'], ['status'], ['kind'], ['base_revision', 'baseRevision'], ['base_digest', 'baseDigest'], ['input_fingerprint', 'inputFingerprint'], ['proposal'], ['missing_information', 'missingInformation'], ['exposed_case_ids', 'exposedCaseIds'], ['error'], ['payload_expired', 'payloadExpired']]),
  checks: nullable(mapChecks, value.checks), metrics: mapMetrics(value.metrics),
});
