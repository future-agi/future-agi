export const ACTIVATION_STATE_SCHEMA_VERSION = "activation-state-2026-05-26.v1";

const DEFAULT_PROGRESS = Object.freeze({
  build: "not_started",
  test: "not_started",
  observe: "not_started",
  ship: "not_started",
  improve: "not_started",
});

const PATH_ALIASES = Object.freeze({
  prompts: "prompt",
  workbench: "prompt",
  agents: "agent",
  simulate_agent: "agent",
  observability: "observe",
  traces: "observe",
  model_gateway: "gateway",
  voice_ai: "voice",
  eval: "evals",
  evaluations: "evals",
  dashboard: "dashboards",
  sample_project: "sample",
});

const PRODUCT_PATHS = new Set([
  "prompt",
  "agent",
  "observe",
  "gateway",
  "voice",
  "evals",
  "dashboards",
  "sample",
]);

const ACTIVATION_STAGES = new Set([
  "feature_disabled",
  "workspace_missing",
  "permission_limited",
  "choose_goal",
  "selected_path_unavailable",
  "activated",
  "daily_review",
  "start_prompt",
  "run_prompt_test",
  "save_prompt_version",
  "compare_prompt_versions",
  "prompt_next_loop",
  "create_agent",
  "run_agent_scenario",
  "review_agent_trace",
  "save_agent_eval",
  "agent_create_eval",
  "connect_observability",
  "waiting_for_first_trace",
  "waiting_for_first_trace_sample_available",
  "review_first_trace",
  "create_trace_evaluator",
  "create_trace_dashboard",
  "create_trace_alert",
  "configure_gateway_provider",
  "create_gateway_key",
  "run_gateway_request",
  "review_gateway_log",
  "fix_gateway_failure",
  "add_gateway_policy",
  "create_voice_agent",
  "run_voice_test_call",
  "review_voice_call",
  "add_voice_success_criteria",
  "voice_monitor_calls",
  "create_eval_dataset",
  "add_eval_scorer",
  "run_eval",
  "review_eval_failures",
  "eval_next_loop",
  "open_sample_project",
  "review_sample_signal",
  "connect_real_data",
]);

const isObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export const normalizeProductPath = (path) => {
  if (path === null || path === undefined || path === "") return null;
  const normalized = PATH_ALIASES[path] || path;
  if (!PRODUCT_PATHS.has(normalized)) {
    throw new Error(`Unsupported onboarding product path: ${path}`);
  }
  return normalized;
};

export const isInternalHref = (href) => {
  if (href === null || href === undefined || href === "") return true;
  if (typeof href !== "string") return false;
  return href.startsWith("/") && !href.startsWith("//");
};

const normalizeAnalytics = (raw = {}) => ({
  eventName: raw.event_name || raw.eventName || null,
  source: raw.source || null,
  targetPath: normalizeProductPath(raw.target_path || raw.targetPath || null),
});

export const normalizeActivationAction = (raw) => {
  if (!raw) return null;
  const href = raw.href ?? null;
  const fallbackHref = raw.fallback_href ?? raw.fallbackHref ?? null;
  if (!isInternalHref(href)) {
    throw new Error(`Onboarding action has external href: ${href}`);
  }
  if (!isInternalHref(fallbackHref)) {
    throw new Error(
      `Onboarding action has external fallback href: ${fallbackHref}`,
    );
  }
  return {
    id: raw.id,
    kind: raw.kind,
    title: raw.title,
    description: raw.description,
    href,
    ctaLabel: raw.cta_label ?? raw.ctaLabel ?? "",
    estimatedMinutes: raw.estimated_minutes ?? raw.estimatedMinutes ?? null,
    priority: raw.priority ?? 0,
    blocked: Boolean(raw.blocked),
    blockedReason: raw.blocked_reason ?? raw.blockedReason ?? null,
    requiresPermission:
      raw.requires_permission ?? raw.requiresPermission ?? null,
    completionEvent: raw.completion_event ?? raw.completionEvent ?? null,
    isSample: Boolean(raw.is_sample ?? raw.isSample),
    routeAvailable: Boolean(raw.route_available ?? raw.routeAvailable),
    fallbackHref,
    analytics: normalizeAnalytics(raw.analytics),
  };
};

const normalizeProgress = (raw = {}) => ({
  ...DEFAULT_PROGRESS,
  ...raw,
});

const normalizeStageCopy = (raw) => {
  if (!raw) return null;
  return {
    eyebrow: raw.eyebrow || "Setup",
    title: raw.title || "Open Get Started",
    description:
      raw.description || "The existing setup checklist is available.",
  };
};

const normalizeSignals = (raw = {}) => ({
  providerKeys: raw.provider_keys ?? raw.providerKeys ?? 0,
  datasets: raw.datasets ?? 0,
  evals: raw.evals ?? 0,
  evalRuns: raw.eval_runs ?? raw.evalRuns ?? 0,
  evalSourceCount: raw.eval_source_count ?? raw.evalSourceCount ?? 0,
  evalSourceType: raw.eval_source_type ?? raw.evalSourceType ?? null,
  evalSourceId: raw.eval_source_id ?? raw.evalSourceId ?? null,
  evalSourceName: raw.eval_source_name ?? raw.evalSourceName ?? null,
  evalScorerCount: raw.eval_scorer_count ?? raw.evalScorerCount ?? 0,
  evalScorerId: raw.eval_scorer_id ?? raw.evalScorerId ?? null,
  evalScorerTemplateId:
    raw.eval_scorer_template_id ?? raw.evalScorerTemplateId ?? null,
  evalScorerName: raw.eval_scorer_name ?? raw.evalScorerName ?? null,
  evalGroupCount: raw.eval_group_count ?? raw.evalGroupCount ?? 0,
  evalGroupId: raw.eval_group_id ?? raw.evalGroupId ?? null,
  evalRunCount: raw.eval_run_count ?? raw.evalRunCount ?? 0,
  evalRunId: raw.eval_run_id ?? raw.evalRunId ?? null,
  evalRunStatus: raw.eval_run_status ?? raw.evalRunStatus ?? null,
  evalRunCompletedAt:
    raw.eval_run_completed_at ?? raw.evalRunCompletedAt ?? null,
  evalFailureCount: raw.eval_failure_count ?? raw.evalFailureCount ?? 0,
  evalHasSource: Boolean(raw.eval_has_source ?? raw.evalHasSource),
  evalHasScorer: Boolean(raw.eval_has_scorer ?? raw.evalHasScorer),
  evalHasCompletedRun: Boolean(
    raw.eval_has_completed_run ?? raw.evalHasCompletedRun,
  ),
  evalHasFailures: Boolean(raw.eval_has_failures ?? raw.evalHasFailures),
  evalHasReview: Boolean(raw.eval_has_review ?? raw.evalHasReview),
  evalHasFailureAction: Boolean(
    raw.eval_has_failure_action ?? raw.evalHasFailureAction,
  ),
  evalFirstLoopCompleted: Boolean(
    raw.eval_first_loop_completed ?? raw.evalFirstLoopCompleted,
  ),
  evalIsSampleOnly: Boolean(raw.eval_is_sample_only ?? raw.evalIsSampleOnly),
  evalSampleSourceCount:
    raw.eval_sample_source_count ?? raw.evalSampleSourceCount ?? 0,
  evalPermissionLimited: Boolean(
    raw.eval_permission_limited ?? raw.evalPermissionLimited,
  ),
  promptTemplates: raw.prompt_templates ?? raw.promptTemplates ?? 0,
  promptVersions: raw.prompt_versions ?? raw.promptVersions ?? 0,
  promptComparisons: raw.prompt_comparisons ?? raw.promptComparisons ?? 0,
  firstPromptId: raw.first_prompt_id ?? raw.firstPromptId ?? null,
  latestPromptId: raw.latest_prompt_id ?? raw.latestPromptId ?? null,
  promptSampleTemplates:
    raw.prompt_sample_templates ?? raw.promptSampleTemplates ?? 0,
  agents: raw.agents ?? 0,
  agentPrototypeRuns: raw.agent_prototype_runs ?? raw.agentPrototypeRuns ?? 0,
  agentId: raw.agent_id ?? raw.agentId ?? null,
  agentSource: raw.agent_source ?? raw.agentSource ?? null,
  agentVersionId: raw.agent_version_id ?? raw.agentVersionId ?? null,
  agentScenarioId: raw.agent_scenario_id ?? raw.agentScenarioId ?? null,
  agentTestId: raw.agent_test_id ?? raw.agentTestId ?? null,
  agentExecutionId: raw.agent_execution_id ?? raw.agentExecutionId ?? null,
  agentCallExecutionId:
    raw.agent_call_execution_id ?? raw.agentCallExecutionId ?? null,
  agentGraphExecutionId:
    raw.agent_graph_execution_id ?? raw.agentGraphExecutionId ?? null,
  agentRunStatus: raw.agent_run_status ?? raw.agentRunStatus ?? null,
  agentSampleCount: raw.agent_sample_count ?? raw.agentSampleCount ?? 0,
  agentHasAgent: Boolean(raw.agent_has_agent ?? raw.agentHasAgent),
  agentHasAgentVersion: Boolean(
    raw.agent_has_agent_version ?? raw.agentHasAgentVersion,
  ),
  agentHasScenario: Boolean(raw.agent_has_scenario ?? raw.agentHasScenario),
  agentHasRun: Boolean(raw.agent_has_run ?? raw.agentHasRun),
  agentRunFailed: Boolean(raw.agent_run_failed ?? raw.agentRunFailed),
  agentHasReview: Boolean(raw.agent_has_review ?? raw.agentHasReview),
  agentHasEvalCoverage: Boolean(
    raw.agent_has_eval_coverage ?? raw.agentHasEvalCoverage,
  ),
  agentMultipleScenarios: Boolean(
    raw.agent_multiple_scenarios ?? raw.agentMultipleScenarios,
  ),
  agentFirstLoopCompleted: Boolean(
    raw.agent_first_loop_completed ?? raw.agentFirstLoopCompleted,
  ),
  agentVoiceFeatureUnavailable: Boolean(
    raw.agent_voice_feature_unavailable ?? raw.agentVoiceFeatureUnavailable,
  ),
  observeProjects: raw.observe_projects ?? raw.observeProjects ?? 0,
  traces: raw.traces ?? 0,
  traceReviews: raw.trace_reviews ?? raw.traceReviews ?? 0,
  gatewayKeys: raw.gateway_keys ?? raw.gatewayKeys ?? 0,
  gatewayRequests: raw.gateway_requests ?? raw.gatewayRequests ?? 0,
  gatewayPolicies: raw.gateway_policies ?? raw.gatewayPolicies ?? 0,
  gatewayAvailable: Boolean(raw.gateway_available ?? raw.gatewayAvailable),
  gatewayId: raw.gateway_id ?? raw.gatewayId ?? null,
  gatewayStatus: raw.gateway_status ?? raw.gatewayStatus ?? null,
  gatewayPublicUrl: raw.gateway_public_url ?? raw.gatewayPublicUrl ?? null,
  gatewayProviderCount:
    raw.gateway_provider_count ?? raw.gatewayProviderCount ?? 0,
  gatewayProviderCredentialId:
    raw.gateway_provider_credential_id ??
    raw.gatewayProviderCredentialId ??
    null,
  gatewayProviderName:
    raw.gateway_provider_name ?? raw.gatewayProviderName ?? null,
  gatewayProviderHealthStatus:
    raw.gateway_provider_health_status ??
    raw.gatewayProviderHealthStatus ??
    null,
  gatewayProviderModelCount:
    raw.gateway_provider_model_count ?? raw.gatewayProviderModelCount ?? 0,
  gatewayHasProvider: Boolean(
    raw.gateway_has_provider ?? raw.gatewayHasProvider,
  ),
  gatewayHasKey: Boolean(raw.gateway_has_key ?? raw.gatewayHasKey),
  gatewayKeyId: raw.gateway_key_id ?? raw.gatewayKeyId ?? null,
  gatewayKeyPrefix: raw.gateway_key_prefix ?? raw.gatewayKeyPrefix ?? null,
  gatewayKeyStatus: raw.gateway_key_status ?? raw.gatewayKeyStatus ?? null,
  gatewayHasRequest: Boolean(raw.gateway_has_request ?? raw.gatewayHasRequest),
  gatewayRequestLogId:
    raw.gateway_request_log_id ?? raw.gatewayRequestLogId ?? null,
  gatewayRequestId: raw.gateway_request_id ?? raw.gatewayRequestId ?? null,
  gatewayRequestStatusCode:
    raw.gateway_request_status_code ?? raw.gatewayRequestStatusCode ?? null,
  gatewayRequestIsError: Boolean(
    raw.gateway_request_is_error ?? raw.gatewayRequestIsError,
  ),
  gatewayRequestErrorMessage:
    raw.gateway_request_error_message ?? raw.gatewayRequestErrorMessage ?? null,
  gatewayRequestProvider:
    raw.gateway_request_provider ?? raw.gatewayRequestProvider ?? null,
  gatewayRequestModel:
    raw.gateway_request_model ?? raw.gatewayRequestModel ?? null,
  gatewayRequestResolvedModel:
    raw.gateway_request_resolved_model ??
    raw.gatewayRequestResolvedModel ??
    null,
  gatewayRequestLatencyMs:
    raw.gateway_request_latency_ms ?? raw.gatewayRequestLatencyMs ?? null,
  gatewayRequestCost:
    raw.gateway_request_cost ?? raw.gatewayRequestCost ?? null,
  gatewayRequestCacheHit: Boolean(
    raw.gateway_request_cache_hit ?? raw.gatewayRequestCacheHit,
  ),
  gatewayRequestFallbackUsed: Boolean(
    raw.gateway_request_fallback_used ?? raw.gatewayRequestFallbackUsed,
  ),
  gatewayRequestGuardrailTriggered: Boolean(
    raw.gateway_request_guardrail_triggered ??
      raw.gatewayRequestGuardrailTriggered,
  ),
  gatewayHasReview: Boolean(raw.gateway_has_review ?? raw.gatewayHasReview),
  gatewayReviewedAt: raw.gateway_reviewed_at ?? raw.gatewayReviewedAt ?? null,
  gatewayHasFailureRepair: Boolean(
    raw.gateway_has_failure_repair ?? raw.gatewayHasFailureRepair,
  ),
  gatewayHasPolicy: Boolean(raw.gateway_has_policy ?? raw.gatewayHasPolicy),
  gatewayPolicyType: raw.gateway_policy_type ?? raw.gatewayPolicyType ?? null,
  gatewayPolicyId: raw.gateway_policy_id ?? raw.gatewayPolicyId ?? null,
  gatewayPolicyRoute:
    raw.gateway_policy_route ?? raw.gatewayPolicyRoute ?? null,
  gatewayPolicySynced: Boolean(
    raw.gateway_policy_synced ?? raw.gatewayPolicySynced,
  ),
  gatewayIsSampleOnly: Boolean(
    raw.gateway_is_sample_only ?? raw.gatewayIsSampleOnly,
  ),
  gatewaySampleRequestCount:
    raw.gateway_sample_request_count ?? raw.gatewaySampleRequestCount ?? 0,
  gatewayPermissionLimited: Boolean(
    raw.gateway_permission_limited ?? raw.gatewayPermissionLimited,
  ),
  gatewayGuardBlocked: Boolean(
    raw.gateway_guard_blocked ?? raw.gatewayGuardBlocked,
  ),
  gatewayFirstLoopCompleted: Boolean(
    raw.gateway_first_loop_completed ?? raw.gatewayFirstLoopCompleted,
  ),
  voiceAgents: raw.voice_agents ?? raw.voiceAgents ?? 0,
  voiceSimulations: raw.voice_simulations ?? raw.voiceSimulations ?? 0,
  voiceCalls: raw.voice_calls ?? raw.voiceCalls ?? 0,
  voiceReviews: raw.voice_reviews ?? raw.voiceReviews ?? 0,
  voiceAgentId: raw.voice_agent_id ?? raw.voiceAgentId ?? null,
  voiceAgentName: raw.voice_agent_name ?? raw.voiceAgentName ?? null,
  voiceAgentProvider:
    raw.voice_agent_provider ?? raw.voiceAgentProvider ?? null,
  voiceAgentVersionId:
    raw.voice_agent_version_id ?? raw.voiceAgentVersionId ?? null,
  voiceScenarioId: raw.voice_scenario_id ?? raw.voiceScenarioId ?? null,
  voiceRunTestId: raw.voice_run_test_id ?? raw.voiceRunTestId ?? null,
  voiceTestExecutionId:
    raw.voice_test_execution_id ?? raw.voiceTestExecutionId ?? null,
  voiceCallExecutionId:
    raw.voice_call_execution_id ?? raw.voiceCallExecutionId ?? null,
  voiceCallStatus: raw.voice_call_status ?? raw.voiceCallStatus ?? null,
  voiceCallCompletedAt:
    raw.voice_call_completed_at ?? raw.voiceCallCompletedAt ?? null,
  voiceCallDurationSeconds:
    raw.voice_call_duration_seconds ?? raw.voiceCallDurationSeconds ?? null,
  voiceCallResponseTimeMs:
    raw.voice_call_response_time_ms ?? raw.voiceCallResponseTimeMs ?? null,
  voiceCallInterruptionCount:
    raw.voice_call_interruption_count ?? raw.voiceCallInterruptionCount ?? null,
  voiceTranscriptAvailable: Boolean(
    raw.voice_transcript_available ?? raw.voiceTranscriptAvailable,
  ),
  voiceRecordingAvailable: Boolean(
    raw.voice_recording_available ?? raw.voiceRecordingAvailable,
  ),
  voiceHasAgent: Boolean(raw.voice_has_agent ?? raw.voiceHasAgent),
  voiceHasScenario: Boolean(raw.voice_has_scenario ?? raw.voiceHasScenario),
  voiceHasTest: Boolean(raw.voice_has_test ?? raw.voiceHasTest),
  voiceHasCall: Boolean(raw.voice_has_call ?? raw.voiceHasCall),
  voiceHasCompletedCall: Boolean(
    raw.voice_has_completed_call ?? raw.voiceHasCompletedCall,
  ),
  voiceCallFailed: Boolean(raw.voice_call_failed ?? raw.voiceCallFailed),
  voiceHasReview: Boolean(raw.voice_has_review ?? raw.voiceHasReview),
  voiceHasSuccessCriteria: Boolean(
    raw.voice_has_success_criteria ?? raw.voiceHasSuccessCriteria,
  ),
  voiceFirstLoopCompleted: Boolean(
    raw.voice_first_loop_completed ?? raw.voiceFirstLoopCompleted,
  ),
  voiceIsSampleOnly: Boolean(raw.voice_is_sample_only ?? raw.voiceIsSampleOnly),
  voiceSampleCallCount:
    raw.voice_sample_call_count ?? raw.voiceSampleCallCount ?? 0,
  voicePermissionLimited: Boolean(
    raw.voice_permission_limited ?? raw.voicePermissionLimited,
  ),
  teamInvites: raw.team_invites ?? raw.teamInvites ?? 0,
  dashboards: raw.dashboards ?? 0,
  alerts: raw.alerts ?? 0,
  firstTraceId: raw.first_trace_id ?? raw.firstTraceId ?? null,
  firstObserveId: raw.first_observe_id ?? raw.firstObserveId ?? null,
});

const normalizeAvailablePath = (raw) => {
  const href = raw.href || "";
  if (!isInternalHref(href)) {
    throw new Error(`Onboarding path has external href: ${href}`);
  }
  return {
    id: normalizeProductPath(raw.id),
    label: raw.label,
    description: raw.description,
    status: raw.status,
    href,
    isAvailable: Boolean(raw.is_available ?? raw.isAvailable),
    blockedReason: raw.blocked_reason ?? raw.blockedReason ?? null,
    requiresPermission:
      raw.requires_permission ?? raw.requiresPermission ?? null,
    firstActionId: raw.first_action_id ?? raw.firstActionId ?? null,
  };
};

const normalizeAvailableGoal = (raw) => ({
  id: raw.id || raw.goal,
  goal: raw.goal,
  primaryPath: normalizeProductPath(raw.primary_path ?? raw.primaryPath),
  label: raw.label,
  description: raw.description,
  estimatedMinutes: raw.estimated_minutes ?? raw.estimatedMinutes ?? null,
  disabled: Boolean(raw.disabled),
  disabledReason: raw.disabled_reason ?? raw.disabledReason ?? null,
});

const normalizeSampleProject = (raw = {}) => {
  const href = raw.href ?? null;
  if (!isInternalHref(href)) {
    throw new Error(`Sample project has external href: ${href}`);
  }
  const entryRoute = raw.entry_route ?? raw.entryRoute ?? null;
  if (!isInternalHref(entryRoute)) {
    throw new Error(`Sample project has external entry route: ${entryRoute}`);
  }
  const entryRoutes = Array.isArray(raw.entry_routes)
    ? raw.entry_routes
    : raw.entryRoutes || [];
  entryRoutes.forEach((route) => {
    if (!isInternalHref(route)) {
      throw new Error(`Sample project has external entry route: ${route}`);
    }
  });
  const realSetupHref =
    raw.real_setup_href ?? raw.realSetupHref ?? "/dashboard/observe";
  if (!isInternalHref(realSetupHref)) {
    throw new Error(`Sample project has external setup href: ${realSetupHref}`);
  }
  return {
    available: Boolean(raw.available),
    created: Boolean(raw.created),
    status: raw.status || "not_created",
    href,
    version: raw.version ?? null,
    manifestId: raw.manifest_id ?? raw.manifestId ?? null,
    manifestVersion: raw.manifest_version ?? raw.manifestVersion ?? null,
    label: raw.label || "Sample",
    entryRoute,
    isRepairable: Boolean(raw.is_repairable ?? raw.isRepairable),
    blockedReason: raw.blocked_reason ?? raw.blockedReason ?? null,
    artifactRefs: raw.artifact_refs ?? raw.artifactRefs ?? {},
    health: raw.health ?? {},
    realSetupHref,
    isHidden: Boolean(raw.is_hidden ?? raw.isHidden),
    hiddenReason: raw.hidden_reason ?? raw.hiddenReason ?? null,
    entryRoutes,
    missingArtifacts: Array.isArray(raw.missing_artifacts)
      ? raw.missing_artifacts
      : raw.missingArtifacts || [],
    lastOpenedAt: raw.last_opened_at ?? raw.lastOpenedAt ?? null,
  };
};

const normalizePromptState = (raw) => {
  if (!raw) return null;
  const isSample = Boolean(raw.is_sample ?? raw.isSample);
  if (isSample && Boolean(raw.has_real_prompt ?? raw.hasRealPrompt)) {
    throw new Error("Sample prompt state cannot count as a real prompt");
  }
  return {
    promptId: raw.prompt_id ?? raw.promptId ?? null,
    promptName: raw.prompt_name ?? raw.promptName ?? null,
    stage: raw.stage ?? null,
    hasRealPrompt: Boolean(raw.has_real_prompt ?? raw.hasRealPrompt),
    hasTestRun: Boolean(raw.has_test_run ?? raw.hasTestRun),
    hasCommittedVersion: Boolean(
      raw.has_committed_version ?? raw.hasCommittedVersion,
    ),
    hasComparison: Boolean(raw.has_comparison ?? raw.hasComparison),
    hasNextLoopAction: Boolean(
      raw.has_next_loop_action ?? raw.hasNextLoopAction,
    ),
    isSample,
    samplePromptCount: raw.sample_prompt_count ?? raw.samplePromptCount ?? 0,
    diagnostics: raw.diagnostics ?? [],
  };
};

const normalizeAgentState = (raw) => {
  if (!raw) return null;
  const isSample = Boolean(raw.is_sample ?? raw.isSample);
  if (isSample && Boolean(raw.has_agent ?? raw.hasAgent)) {
    throw new Error("Sample agent state cannot count as a real agent");
  }
  return {
    agentId: raw.agent_id ?? raw.agentId ?? null,
    agentSource: raw.agent_source ?? raw.agentSource ?? null,
    agentVersionId: raw.agent_version_id ?? raw.agentVersionId ?? null,
    scenarioId: raw.scenario_id ?? raw.scenarioId ?? null,
    testId: raw.test_id ?? raw.testId ?? null,
    executionId: raw.execution_id ?? raw.executionId ?? null,
    callExecutionId: raw.call_execution_id ?? raw.callExecutionId ?? null,
    graphExecutionId: raw.graph_execution_id ?? raw.graphExecutionId ?? null,
    runStatus: raw.run_status ?? raw.runStatus ?? null,
    runCompletedAt: raw.run_completed_at ?? raw.runCompletedAt ?? null,
    stage: raw.stage ?? null,
    hasAgent: Boolean(raw.has_agent ?? raw.hasAgent),
    hasAgentVersion: Boolean(raw.has_agent_version ?? raw.hasAgentVersion),
    hasScenario: Boolean(raw.has_scenario ?? raw.hasScenario),
    hasRun: Boolean(raw.has_run ?? raw.hasRun),
    hasReview: Boolean(raw.has_review ?? raw.hasReview),
    hasEvalCoverage: Boolean(raw.has_eval_coverage ?? raw.hasEvalCoverage),
    isSample,
    sampleAgentCount: raw.sample_agent_count ?? raw.sampleAgentCount ?? 0,
    voiceFeatureUnavailable: Boolean(
      raw.voice_feature_unavailable ?? raw.voiceFeatureUnavailable,
    ),
    permissionLimited: Boolean(raw.permission_limited ?? raw.permissionLimited),
    diagnostics: raw.diagnostics ?? [],
  };
};

const normalizeVoiceState = (raw) => {
  if (!raw) return null;
  const isSample = Boolean(raw.is_sample ?? raw.isSample);
  if (isSample && Boolean(raw.has_call ?? raw.hasCall)) {
    throw new Error("Sample voice call state cannot count as a real call");
  }
  return {
    agentId: raw.agent_id ?? raw.agentId ?? null,
    agentName: raw.agent_name ?? raw.agentName ?? null,
    agentProvider: raw.agent_provider ?? raw.agentProvider ?? null,
    agentVersionId: raw.agent_version_id ?? raw.agentVersionId ?? null,
    scenarioId: raw.scenario_id ?? raw.scenarioId ?? null,
    runTestId: raw.run_test_id ?? raw.runTestId ?? null,
    testExecutionId: raw.test_execution_id ?? raw.testExecutionId ?? null,
    callExecutionId: raw.call_execution_id ?? raw.callExecutionId ?? null,
    callStatus: raw.call_status ?? raw.callStatus ?? null,
    callCompletedAt: raw.call_completed_at ?? raw.callCompletedAt ?? null,
    callDurationSeconds:
      raw.call_duration_seconds ?? raw.callDurationSeconds ?? null,
    callResponseTimeMs:
      raw.call_response_time_ms ?? raw.callResponseTimeMs ?? null,
    callInterruptionCount:
      raw.call_interruption_count ?? raw.callInterruptionCount ?? null,
    transcriptAvailable: Boolean(
      raw.transcript_available ?? raw.transcriptAvailable,
    ),
    recordingAvailable: Boolean(
      raw.recording_available ?? raw.recordingAvailable,
    ),
    reviewedAt: raw.reviewed_at ?? raw.reviewedAt ?? null,
    successCriteriaAt: raw.success_criteria_at ?? raw.successCriteriaAt ?? null,
    evalConfigId: raw.eval_config_id ?? raw.evalConfigId ?? null,
    stage: raw.stage ?? null,
    hasAgent: Boolean(raw.has_agent ?? raw.hasAgent),
    hasScenario: Boolean(raw.has_scenario ?? raw.hasScenario),
    hasTest: Boolean(raw.has_test ?? raw.hasTest),
    hasCall: Boolean(raw.has_call ?? raw.hasCall),
    hasCompletedCall: Boolean(raw.has_completed_call ?? raw.hasCompletedCall),
    callFailed: Boolean(raw.call_failed ?? raw.callFailed),
    hasReview: Boolean(raw.has_review ?? raw.hasReview),
    hasSuccessCriteria: Boolean(
      raw.has_success_criteria ?? raw.hasSuccessCriteria,
    ),
    isSample,
    sampleCallCount: raw.sample_call_count ?? raw.sampleCallCount ?? 0,
    permissionLimited: Boolean(raw.permission_limited ?? raw.permissionLimited),
    diagnostics: raw.diagnostics ?? [],
  };
};

const normalizeGatewayState = (raw) => {
  if (!raw) return null;
  const isSample = Boolean(raw.is_sample ?? raw.isSample);
  if (isSample && Boolean(raw.has_request ?? raw.hasRequest)) {
    throw new Error(
      "Sample gateway request state cannot count as a real request",
    );
  }
  return {
    gatewayAvailable: Boolean(raw.gateway_available ?? raw.gatewayAvailable),
    gatewayId: raw.gateway_id ?? raw.gatewayId ?? null,
    gatewayStatus: raw.gateway_status ?? raw.gatewayStatus ?? null,
    gatewayPublicUrl: raw.gateway_public_url ?? raw.gatewayPublicUrl ?? null,
    providerCount: raw.provider_count ?? raw.providerCount ?? 0,
    providerCredentialId:
      raw.provider_credential_id ?? raw.providerCredentialId ?? null,
    providerName: raw.provider_name ?? raw.providerName ?? null,
    providerHealthStatus:
      raw.provider_health_status ?? raw.providerHealthStatus ?? null,
    providerModelCount: raw.provider_model_count ?? raw.providerModelCount ?? 0,
    hasProvider: Boolean(raw.has_provider ?? raw.hasProvider),
    hasKey: Boolean(raw.has_key ?? raw.hasKey),
    gatewayKeyId: raw.gateway_key_id ?? raw.gatewayKeyId ?? null,
    keyPrefix: raw.key_prefix ?? raw.keyPrefix ?? null,
    keyStatus: raw.key_status ?? raw.keyStatus ?? null,
    hasRequest: Boolean(raw.has_request ?? raw.hasRequest),
    requestLogId: raw.request_log_id ?? raw.requestLogId ?? null,
    requestId: raw.request_id ?? raw.requestId ?? null,
    requestStatusCode: raw.request_status_code ?? raw.requestStatusCode ?? null,
    requestIsError: Boolean(raw.request_is_error ?? raw.requestIsError),
    requestErrorMessage:
      raw.request_error_message ?? raw.requestErrorMessage ?? null,
    requestProvider: raw.request_provider ?? raw.requestProvider ?? null,
    requestModel: raw.request_model ?? raw.requestModel ?? null,
    requestResolvedModel:
      raw.request_resolved_model ?? raw.requestResolvedModel ?? null,
    requestLatencyMs: raw.request_latency_ms ?? raw.requestLatencyMs ?? null,
    requestCost: raw.request_cost ?? raw.requestCost ?? null,
    requestCacheHit: Boolean(raw.request_cache_hit ?? raw.requestCacheHit),
    requestFallbackUsed: Boolean(
      raw.request_fallback_used ?? raw.requestFallbackUsed,
    ),
    requestGuardrailTriggered: Boolean(
      raw.request_guardrail_triggered ?? raw.requestGuardrailTriggered,
    ),
    hasReview: Boolean(raw.has_review ?? raw.hasReview),
    reviewedAt: raw.reviewed_at ?? raw.reviewedAt ?? null,
    hasFailureRepair: Boolean(raw.has_failure_repair ?? raw.hasFailureRepair),
    hasPolicy: Boolean(raw.has_policy ?? raw.hasPolicy),
    policyType: raw.policy_type ?? raw.policyType ?? null,
    policyId: raw.policy_id ?? raw.policyId ?? null,
    policyRoute: raw.policy_route ?? raw.policyRoute ?? null,
    policySynced: Boolean(raw.policy_synced ?? raw.policySynced),
    isSample,
    sampleRequestCount: raw.sample_request_count ?? raw.sampleRequestCount ?? 0,
    permissionLimited: Boolean(raw.permission_limited ?? raw.permissionLimited),
    guardBlocked: Boolean(raw.guard_blocked ?? raw.guardBlocked),
    diagnostics: raw.diagnostics ?? [],
    stage: raw.stage ?? null,
  };
};

const normalizeEvalState = (raw) => {
  if (!raw) return null;
  const isSample = Boolean(raw.is_sample ?? raw.isSample);
  if (isSample && Boolean(raw.has_source ?? raw.hasSource)) {
    throw new Error("Sample eval source state cannot count as a real source");
  }
  return {
    sourceType: raw.source_type ?? raw.sourceType ?? null,
    sourceId: raw.source_id ?? raw.sourceId ?? null,
    sourceName: raw.source_name ?? raw.sourceName ?? null,
    scorerId: raw.scorer_id ?? raw.scorerId ?? null,
    scorerTemplateId: raw.scorer_template_id ?? raw.scorerTemplateId ?? null,
    scorerName: raw.scorer_name ?? raw.scorerName ?? null,
    evalGroupId: raw.eval_group_id ?? raw.evalGroupId ?? null,
    runId: raw.run_id ?? raw.runId ?? null,
    runStatus: raw.run_status ?? raw.runStatus ?? null,
    runCompletedAt: raw.run_completed_at ?? raw.runCompletedAt ?? null,
    failureCount: raw.failure_count ?? raw.failureCount ?? 0,
    reviewedAt: raw.reviewed_at ?? raw.reviewedAt ?? null,
    failureActionAt: raw.failure_action_at ?? raw.failureActionAt ?? null,
    stage: raw.stage ?? null,
    hasSource: Boolean(raw.has_source ?? raw.hasSource),
    hasScorer: Boolean(raw.has_scorer ?? raw.hasScorer),
    hasCompletedRun: Boolean(raw.has_completed_run ?? raw.hasCompletedRun),
    hasFailures: Boolean(raw.has_failures ?? raw.hasFailures),
    hasReview: Boolean(raw.has_review ?? raw.hasReview),
    hasFailureAction: Boolean(raw.has_failure_action ?? raw.hasFailureAction),
    isSample,
    sampleSourceCount: raw.sample_source_count ?? raw.sampleSourceCount ?? 0,
    permissionLimited: Boolean(raw.permission_limited ?? raw.permissionLimited),
    diagnostics: raw.diagnostics ?? [],
  };
};

export const hasSampleRoute = (sampleProject) =>
  Boolean(
    sampleProject &&
      !sampleProject.isHidden &&
      (sampleProject.entryRoute || sampleProject.entryRoutes?.length),
  );

export const isSampleHidden = (sampleProject) =>
  Boolean(sampleProject?.isHidden || sampleProject?.status === "hidden");

export const canOpenSample = (sampleProject) =>
  Boolean(
    sampleProject?.available &&
      !isSampleHidden(sampleProject) &&
      !["unavailable", "repair_failed"].includes(sampleProject.status),
  );

export const shouldShowSampleAsPrimary = (state) =>
  Boolean(
    state?.primaryPath === "sample" ||
      (state?.recommendedAction?.blocked &&
        hasSampleRoute(state.sampleProject)),
  );

const normalizeEmailEligibility = (raw = {}) => ({
  eligible: Boolean(raw.eligible),
  suppressed: Boolean(raw.suppressed),
  suppressionReason: raw.suppression_reason ?? raw.suppressionReason ?? null,
  nextEmailKey: raw.next_email_key ?? raw.nextEmailKey ?? null,
  nextEmailAfter: raw.next_email_after ?? raw.nextEmailAfter ?? null,
  digestEligible: Boolean(raw.digest_eligible ?? raw.digestEligible),
  lastEmailSentAt: raw.last_email_sent_at ?? raw.lastEmailSentAt ?? null,
  frequencyCapRemaining:
    raw.frequency_cap_remaining ?? raw.frequencyCapRemaining ?? 0,
  dryRunOnly: Boolean(raw.dry_run_only ?? raw.dryRunOnly),
});

const normalizeDailyQualitySignal = (raw) => {
  if (!raw) return null;
  const route = raw.route ?? "";
  if (!isInternalHref(route)) {
    throw new Error(`Daily quality signal has external route: ${route}`);
  }
  const isSample = Boolean(raw.is_sample ?? raw.isSample);
  if (isSample) {
    throw new Error("Daily quality signal cannot use sample data");
  }
  return {
    id: raw.id,
    type: raw.type,
    severity: raw.severity || "info",
    title: raw.title,
    body: raw.body,
    sourceType: raw.source_type ?? raw.sourceType ?? null,
    sourceId: raw.source_id ?? raw.sourceId ?? null,
    projectId: raw.project_id ?? raw.projectId ?? null,
    route,
    isSample,
    createdAt: raw.created_at ?? raw.createdAt ?? null,
  };
};

const normalizeDailyQualityAction = (raw) => {
  if (!raw) return null;
  const route = raw.route ?? "";
  const fallbackRoute = raw.fallback_route ?? raw.fallbackRoute ?? "";
  if (!isInternalHref(route)) {
    throw new Error(`Daily quality action has external route: ${route}`);
  }
  if (!isInternalHref(fallbackRoute)) {
    throw new Error(
      `Daily quality action has external fallback route: ${fallbackRoute}`,
    );
  }
  const isSample = Boolean(raw.is_sample ?? raw.isSample);
  if (isSample) {
    throw new Error("Daily quality action cannot use sample data");
  }
  return {
    id: raw.id,
    label: raw.label,
    body: raw.body,
    route,
    fallbackRoute,
    routeAvailable: Boolean(raw.route_available ?? raw.routeAvailable ?? true),
    sourceType: raw.source_type ?? raw.sourceType ?? null,
    sourceId: raw.source_id ?? raw.sourceId ?? null,
    assignedToUserId: raw.assigned_to_user_id ?? raw.assignedToUserId ?? null,
    assignedToName: raw.assigned_to_name ?? raw.assignedToName ?? null,
    assignedAt: raw.assigned_at ?? raw.assignedAt ?? null,
    dueAt: raw.due_at ?? raw.dueAt ?? null,
    isOverdue: Boolean(raw.is_overdue ?? raw.isOverdue),
    successEvent: raw.success_event ?? raw.successEvent ?? null,
    isPrimary: Boolean(raw.is_primary ?? raw.isPrimary),
    isSample,
    requiresPermission:
      raw.requires_permission ?? raw.requiresPermission ?? null,
    activationKind: raw.activation_kind ?? raw.activationKind ?? null,
  };
};

const normalizeDailyQualityProductCard = (raw) => {
  const route = raw.route ?? "";
  if (!isInternalHref(route)) {
    throw new Error(`Daily quality product card has external route: ${route}`);
  }
  return {
    path: normalizeProductPath(raw.path),
    status: raw.status,
    label: raw.label,
    summary: raw.summary,
    metric: raw.metric,
    change: raw.change ?? null,
    route,
  };
};

const normalizeDailyQualityWeeklyReview = (raw) => {
  if (!raw) return null;
  const route = raw.route ?? "";
  if (!isInternalHref(route)) {
    throw new Error(`Weekly quality review has external route: ${route}`);
  }
  return {
    due: Boolean(raw.due),
    status: raw.status,
    route,
    window: {
      startAt: raw.window?.start_at ?? raw.window?.startAt ?? null,
      endAt: raw.window?.end_at ?? raw.window?.endAt ?? null,
    },
    summary: raw.summary,
    unresolvedCount: raw.unresolved_count ?? raw.unresolvedCount ?? 0,
    completedCount: raw.completed_count ?? raw.completedCount ?? 0,
    lastCompletedAt: raw.last_completed_at ?? raw.lastCompletedAt ?? null,
    actionLabel: raw.action_label ?? raw.actionLabel ?? null,
  };
};

const normalizeDailyQuality = (raw) => {
  if (!raw) return null;
  return {
    mode: raw.mode,
    lastReviewedAt: raw.last_reviewed_at ?? raw.lastReviewedAt ?? null,
    window: {
      startAt: raw.window?.start_at ?? raw.window?.startAt ?? null,
      endAt: raw.window?.end_at ?? raw.window?.endAt ?? null,
    },
    topSignal: normalizeDailyQualitySignal(
      raw.top_signal ?? raw.topSignal ?? null,
    ),
    primaryAction: normalizeDailyQualityAction(
      raw.primary_action ?? raw.primaryAction ?? null,
    ),
    actionCards: (raw.action_cards ?? raw.actionCards ?? []).map(
      normalizeDailyQualityAction,
    ),
    productCards: (raw.product_cards ?? raw.productCards ?? []).map(
      normalizeDailyQualityProductCard,
    ),
    weeklyReview: normalizeDailyQualityWeeklyReview(
      raw.weekly_review ?? raw.weeklyReview ?? null,
    ),
    digestEligible: Boolean(raw.digest_eligible ?? raw.digestEligible),
    digestSuppressionReason:
      raw.digest_suppression_reason ?? raw.digestSuppressionReason ?? null,
    diagnostics: raw.diagnostics ?? [],
  };
};

const normalizePermissions = (raw = {}) => ({
  role: raw.role ?? null,
  canRead: Boolean(raw.can_read ?? raw.canRead),
  canWrite: Boolean(raw.can_write ?? raw.canWrite),
  canManageWorkspace: Boolean(
    raw.can_manage_workspace ?? raw.canManageWorkspace,
  ),
  missingPermissions: Array.isArray(raw.missing_permissions)
    ? raw.missing_permissions
    : raw.missingPermissions || [],
  requestAccessHref: raw.request_access_href ?? raw.requestAccessHref ?? null,
  permissionLimited: Boolean(raw.permission_limited ?? raw.permissionLimited),
});

const normalizeRouteAvailability = (raw = {}) =>
  Object.fromEntries(
    Object.entries(raw).map(([key, value]) => {
      const href = value?.href || "";
      if (!isInternalHref(href)) {
        throw new Error(`Route availability has external href: ${href}`);
      }
      return [
        key,
        {
          href,
          isAvailable: Boolean(value?.is_available ?? value?.isAvailable),
          reason: value?.reason ?? null,
        },
      ];
    }),
  );

const normalizeEmailContext = (raw) => {
  if (!raw) return null;
  return {
    campaignKey: raw.campaign_key ?? raw.campaignKey ?? null,
    emailKey: raw.email_key ?? raw.emailKey ?? null,
    targetStage: raw.target_stage ?? raw.targetStage ?? null,
    targetEvent: raw.target_event ?? raw.targetEvent ?? null,
    targetRoute: raw.target_route ?? raw.targetRoute ?? null,
    contextStatus: raw.context_status ?? raw.contextStatus ?? null,
    staleReason: raw.stale_reason ?? raw.staleReason ?? null,
    resolvedHref: raw.resolved_href ?? raw.resolvedHref ?? null,
  };
};

const normalizeLastMeaningfulEvent = (raw) => {
  if (!raw) return null;
  return {
    name: raw.name,
    occurredAt: raw.occurred_at ?? raw.occurredAt ?? null,
    isSample: Boolean(raw.is_sample ?? raw.isSample),
    path: normalizeProductPath(raw.path),
    metadata: raw.metadata || {},
  };
};

const normalizeDiagnostics = (raw) => {
  if (!raw) return null;
  return {
    resolverVersion: raw.resolver_version ?? raw.resolverVersion ?? null,
    decisionReason: raw.decision_reason ?? raw.decisionReason ?? null,
    matchedRule: raw.matched_rule ?? raw.matchedRule ?? null,
    candidateActions: raw.candidate_actions ?? raw.candidateActions ?? [],
    suppressedActions:
      raw.suppressed_actions?.map((item) => ({
        id: item.id,
        reason: item.reason,
      })) ??
      raw.suppressedActions ??
      [],
    evaluatedAt: raw.evaluated_at ?? raw.evaluatedAt ?? null,
  };
};

const routeHrefs = (routeAvailability) =>
  new Set(Object.values(routeAvailability).map((route) => route.href));

const assertActionRoute = (action, routeAvailability, label) => {
  if (!action) return;
  if (action.routeAvailable && action.href) {
    const hrefs = routeHrefs(routeAvailability);
    if (!hrefs.has(action.href)) {
      throw new Error(`${label} href is missing from route availability`);
    }
  }
  if (action.blocked && !action.blockedReason) {
    throw new Error(`${label} is blocked without a blocked reason`);
  }
};

export const hasOnePrimaryAction = (state) => {
  if (!state?.recommendedAction) return false;
  if (!state?.fallbackAction) return false;
  return (
    state.recommendedAction.id !== state.fallbackAction.id ||
    state.stage === "feature_disabled"
  );
};

export const normalizeActivationState = (raw) => {
  if (!isObject(raw)) {
    throw new Error("Activation state must be an object");
  }
  if (raw.schema_version !== ACTIVATION_STATE_SCHEMA_VERSION) {
    throw new Error("Unsupported activation-state schema version");
  }
  if (!ACTIVATION_STAGES.has(raw.stage)) {
    throw new Error(`Unsupported activation stage: ${raw.stage}`);
  }

  const routeAvailability = normalizeRouteAvailability(raw.route_availability);
  const recommendedAction = normalizeActivationAction(raw.recommended_action);
  const fallbackAction = normalizeActivationAction(raw.fallback_action);
  const primaryPath = normalizeProductPath(raw.primary_path);

  const state = {
    schemaVersion: raw.schema_version,
    requestId: raw.request_id,
    serverTime: raw.server_time,
    workspaceId: raw.workspace_id ?? null,
    organizationId: raw.organization_id ?? null,
    userId: raw.user_id,
    goal: raw.goal ?? null,
    persona: raw.persona ?? null,
    primaryPath,
    stage: raw.stage,
    stageCopy: normalizeStageCopy(raw.stage_copy ?? raw.stageCopy),
    homeMode: raw.home_mode,
    isActivated: Boolean(raw.is_activated),
    activatedAt: raw.activated_at ?? null,
    recommendedAction,
    fallbackAction,
    progress: normalizeProgress(raw.progress),
    signals: normalizeSignals(raw.signals),
    availableGoals: (raw.available_goals || raw.availableGoals || []).map(
      normalizeAvailableGoal,
    ),
    availablePaths: (raw.available_paths || []).map(normalizeAvailablePath),
    sampleProject: normalizeSampleProject(raw.sample_project),
    prompt: normalizePromptState(raw.prompt),
    agent: normalizeAgentState(raw.agent),
    eval: normalizeEvalState(raw.eval),
    voice: normalizeVoiceState(raw.voice),
    gateway: normalizeGatewayState(raw.gateway),
    dailyQuality: normalizeDailyQuality(raw.daily_quality ?? raw.dailyQuality),
    emailEligibility: normalizeEmailEligibility(raw.email_eligibility),
    permissions: normalizePermissions(raw.permissions),
    featureFlags: raw.feature_flags || {},
    routeAvailability,
    emailContext: normalizeEmailContext(raw.email_context),
    lastMeaningfulEvent: normalizeLastMeaningfulEvent(
      raw.last_meaningful_event,
    ),
    diagnostics: normalizeDiagnostics(raw.diagnostics),
    warnings: raw.warnings || [],
  };

  if (state.stage !== "workspace_missing" && !state.recommendedAction) {
    throw new Error(
      "Renderable activation state requires a recommended action",
    );
  }
  if (!state.fallbackAction) {
    throw new Error("Activation state requires a fallback action");
  }
  if (!hasOnePrimaryAction(state)) {
    throw new Error("Activation state must expose one primary action");
  }

  assertActionRoute(
    state.recommendedAction,
    routeAvailability,
    "Primary action",
  );
  assertActionRoute(state.fallbackAction, routeAvailability, "Fallback action");

  return state;
};

export const validateActivationStateFixture = (state) => {
  normalizeActivationState(state);
  return true;
};

export const makeActivationStateErrorFallback = (error) => {
  const message =
    error?.result?.message ||
    error?.message ||
    error?.detail ||
    "Activation state could not be loaded";
  const getStartedRoute = {
    href: "/dashboard/get-started",
    is_available: true,
    reason: null,
  };
  const action = {
    id: "open_get_started",
    kind: "fallback",
    title: "Open Get Started",
    description: "Use the existing setup checklist.",
    href: "/dashboard/get-started",
    cta_label: "Open Get Started",
    estimated_minutes: null,
    priority: 10,
    blocked: false,
    blocked_reason: null,
    requires_permission: null,
    completion_event: null,
    is_sample: false,
    route_available: true,
    fallback_href: "/dashboard/get-started",
    analytics: {
      event_name: "onboarding_recommended_action_clicked",
      source: "api_error",
      target_path: null,
    },
  };
  return normalizeActivationState({
    schema_version: ACTIVATION_STATE_SCHEMA_VERSION,
    request_id: "local_api_error_fallback",
    server_time: new Date(0).toISOString(),
    workspace_id: null,
    organization_id: null,
    user_id: "unknown",
    goal: null,
    persona: null,
    primary_path: null,
    stage: "feature_disabled",
    home_mode: "fallback",
    is_activated: false,
    activated_at: null,
    recommended_action: action,
    fallback_action: action,
    progress: DEFAULT_PROGRESS,
    signals: {},
    available_paths: [],
    sample_project: {
      available: false,
      created: false,
      status: "unavailable",
      href: null,
      version: null,
      is_hidden: true,
      hidden_reason: "api_error",
      entry_routes: [],
      missing_artifacts: [],
      last_opened_at: null,
    },
    email_eligibility: {
      eligible: false,
      suppressed: true,
      suppression_reason: "feature_disabled",
      next_email_key: null,
      next_email_after: null,
      digest_eligible: false,
      last_email_sent_at: null,
      frequency_cap_remaining: 0,
      dry_run_only: true,
    },
    permissions: {
      role: null,
      can_read: false,
      can_write: false,
      can_manage_workspace: false,
      missing_permissions: [],
      request_access_href: "/dashboard/settings/user-management",
      permission_limited: false,
    },
    feature_flags: {
      onboarding_activation_state_api: false,
    },
    route_availability: {
      get_started: getStartedRoute,
    },
    email_context: null,
    last_meaningful_event: null,
    diagnostics: null,
    warnings: ["activation_state_request_failed", message],
  });
};
