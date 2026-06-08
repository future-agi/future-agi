import {
  appendSetupQuickStartAttributionToHref,
  setupQuickStartAttributionParams,
} from "src/sections/auth/jwt/setup-org-quick-starts";

export const VOICE_ONBOARDING_MODES = Object.freeze({
  CREATE_AGENT: "create-voice-agent",
  CREATE_TEST_CALL: "create-test-call",
  RUN_TEST_CALL: "run-test-call",
  REVIEW_CALL: "review-voice-call",
  SUCCESS_CRITERIA: "success-criteria",
  MONITOR_CALLS: "monitor-calls",
});

const STAGE_BY_MODE = Object.freeze({
  [VOICE_ONBOARDING_MODES.CREATE_AGENT]: "create_voice_agent",
  [VOICE_ONBOARDING_MODES.CREATE_TEST_CALL]: "run_voice_test_call",
  [VOICE_ONBOARDING_MODES.RUN_TEST_CALL]: "run_voice_test_call",
  [VOICE_ONBOARDING_MODES.REVIEW_CALL]: "review_voice_call",
  [VOICE_ONBOARDING_MODES.SUCCESS_CRITERIA]: "add_voice_success_criteria",
  [VOICE_ONBOARDING_MODES.MONITOR_CALLS]: "voice_monitor_calls",
});
const MODE_BY_JOURNEY_STEP = Object.freeze({
  add_voice_success_criteria: VOICE_ONBOARDING_MODES.SUCCESS_CRITERIA,
  create_voice_agent: VOICE_ONBOARDING_MODES.CREATE_AGENT,
  review_voice_call: VOICE_ONBOARDING_MODES.REVIEW_CALL,
  run_voice_test_call: VOICE_ONBOARDING_MODES.RUN_TEST_CALL,
  voice_monitor_calls: VOICE_ONBOARDING_MODES.MONITOR_CALLS,
});

const compactMetadata = (metadata) =>
  Object.fromEntries(
    Object.entries(metadata).filter(
      ([, value]) => value != null && value !== "",
    ),
  );

const toSearchParams = (search = "") =>
  search instanceof URLSearchParams
    ? search
    : typeof search === "string" && search.startsWith("?")
      ? new URLSearchParams(search.slice(1))
      : new URLSearchParams(search);

export const voiceSetupQuickStartAttributionFromSearch = (search = "") => {
  const params = toSearchParams(search);
  return {
    quick_start_goal: params.get("quick_start_goal"),
    quick_start_id: params.get("quick_start_id"),
    quick_start_primary_path: params.get("quick_start_primary_path"),
  };
};

export const appendVoiceOnboardingAttributionToHref = (
  href,
  attributionOrSearch = {},
) =>
  appendSetupQuickStartAttributionToHref(
    href,
    attributionOrSearch instanceof URLSearchParams ||
      typeof attributionOrSearch === "string"
      ? voiceSetupQuickStartAttributionFromSearch(attributionOrSearch)
      : attributionOrSearch,
  );

const voiceQuickStartAttributionInput = ({
  quickStartAttribution,
  search,
} = {}) =>
  quickStartAttribution || voiceSetupQuickStartAttributionFromSearch(search);

export const getVoiceOnboardingParams = (search = "") => {
  const params = toSearchParams(search);
  const journeyMode = MODE_BY_JOURNEY_STEP[params.get("journey_step")] || "";
  return {
    mode: params.get("onboarding") || journeyMode,
    from: params.get("from") || (journeyMode ? "onboarding" : ""),
    callId: params.get("call_id") || "",
    agentDefinitionId: params.get("agent_definition_id") || "",
    tourAnchor: params.get("tour_anchor"),
  };
};

const appendVoiceAgentDefinitionParam = (params, agentDefinitionId) => {
  if (agentDefinitionId) {
    params.set("agent_definition_id", agentDefinitionId);
  }
};

export const buildVoiceCreateTestHref = ({
  agentDefinitionId,
  quickStartAttribution,
  search,
} = {}) => {
  const params = new URLSearchParams();
  params.set("from", "onboarding");
  params.set("onboarding", VOICE_ONBOARDING_MODES.CREATE_TEST_CALL);
  appendVoiceAgentDefinitionParam(params, agentDefinitionId);

  return appendVoiceOnboardingAttributionToHref(
    `/dashboard/simulate/test?${params.toString()}`,
    voiceQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildVoiceRunTestHref = ({
  agentDefinitionId,
  quickStartAttribution,
  search,
  testId,
} = {}) => {
  if (!testId) return null;
  const params = new URLSearchParams();
  params.set("from", "onboarding");
  params.set("onboarding", VOICE_ONBOARDING_MODES.RUN_TEST_CALL);
  appendVoiceAgentDefinitionParam(params, agentDefinitionId);

  return appendVoiceOnboardingAttributionToHref(
    `/dashboard/simulate/test/${testId}/runs?${params.toString()}`,
    voiceQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildVoiceReviewCallHref = ({
  agentDefinitionId,
  callId,
  executionId,
  quickStartAttribution,
  search,
  testId,
} = {}) => {
  if (!testId || !executionId) return null;
  const params = new URLSearchParams();
  params.set("from", "onboarding");
  params.set("onboarding", VOICE_ONBOARDING_MODES.REVIEW_CALL);
  appendVoiceAgentDefinitionParam(params, agentDefinitionId);
  if (callId) params.set("call_id", callId);

  return appendVoiceOnboardingAttributionToHref(
    `/dashboard/simulate/test/${testId}/${executionId}/call-details?${params.toString()}`,
    voiceQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildVoiceSuccessCriteriaHref = ({
  agentDefinitionId,
  callId,
  quickStartAttribution,
  search,
  testId,
} = {}) => {
  if (!testId) return null;
  const params = new URLSearchParams();
  params.set("from", "onboarding");
  params.set("onboarding", VOICE_ONBOARDING_MODES.SUCCESS_CRITERIA);
  appendVoiceAgentDefinitionParam(params, agentDefinitionId);
  if (callId) params.set("call_id", callId);

  return appendVoiceOnboardingAttributionToHref(
    `/dashboard/simulate/test/${testId}/runs?${params.toString()}`,
    voiceQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const isVoiceOnboardingMode = (mode) =>
  Object.values(VOICE_ONBOARDING_MODES).includes(mode);

export const buildVoiceAgentCreatedPayload = ({
  agentDefinitionId,
  provider,
  quickStartAttribution,
} = {}) => ({
  eventName: "voice_agent_created",
  primaryPath: "voice",
  stage: "create_voice_agent",
  source: "voice_agent_definition_create",
  artifactType: "voice_agent",
  artifactId: String(agentDefinitionId || "voice-agent"),
  metadata: compactMetadata({
    agent_definition_id: agentDefinitionId,
    provider,
  }),
  idempotencyKey: [
    "voice_agent_created",
    agentDefinitionId || "voice-agent",
  ].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});

export const buildVoiceRouteFocusPayload = ({
  mode,
  source,
  testId,
  executionId,
  callId,
  agentDefinitionId,
  quickStartAttribution,
}) => {
  if (!isVoiceOnboardingMode(mode)) return null;

  const artifactId =
    testId || executionId || callId || agentDefinitionId || mode;
  return {
    eventName: "onboarding_voice_route_focus_viewed",
    primaryPath: "voice",
    stage: STAGE_BY_MODE[mode] || "create_voice_agent",
    source,
    artifactType: testId ? "voice_test" : "voice_route",
    artifactId: String(artifactId),
    metadata: compactMetadata({
      route_mode: mode,
      test_id: testId,
      execution_id: executionId,
      call_execution_id: callId,
      agent_definition_id: agentDefinitionId,
    }),
    idempotencyKey: [
      "onboarding_voice_route_focus_viewed",
      mode,
      testId || "no-test",
      executionId || "no-execution",
      callId || agentDefinitionId || "no-artifact",
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildVoiceCallReviewedPayload = ({
  testId,
  executionId,
  callId,
  quickStartAttribution,
}) => ({
  eventName: "voice_call_reviewed",
  primaryPath: "voice",
  stage: "review_voice_call",
  source: "voice_call_detail",
  artifactType: "voice_call",
  artifactId: String(callId || executionId),
  metadata: compactMetadata({
    test_id: testId,
    execution_id: executionId,
    call_execution_id: callId,
  }),
  idempotencyKey: [
    "voice_call_reviewed",
    testId,
    executionId,
    callId || "call-details",
  ].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});

export const voiceCallIdFromExecution = (execution = {}) =>
  execution.call_id ||
  execution.callId ||
  execution.call_execution_id ||
  execution.callExecutionId ||
  execution.trace_id ||
  execution.traceId ||
  execution.id ||
  "";

export const buildVoiceTestCallCompletedPayload = ({
  agentDefinitionId,
  testId,
  executionId,
  callId,
  status,
  quickStartAttribution,
}) => ({
  eventName: "voice_test_call_completed",
  primaryPath: "voice",
  stage: "run_voice_test_call",
  source: "voice_simulation_runs",
  artifactType: "voice_call",
  artifactId: String(callId || executionId),
  metadata: compactMetadata({
    test_id: testId,
    execution_id: executionId,
    call_execution_id: callId,
    agent_definition_id: agentDefinitionId,
    status,
  }),
  idempotencyKey: [
    "voice_test_call_completed",
    testId,
    executionId,
    callId || "call",
  ].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});

export const buildVoiceSuccessCriteriaAddedPayload = ({
  testId,
  callId,
  evalConfig,
  quickStartAttribution,
}) => {
  const evalId =
    evalConfig?.id ||
    evalConfig?.template_id ||
    evalConfig?.templateId ||
    evalConfig?.name ||
    "new";
  return {
    eventName: "voice_success_criteria_added",
    primaryPath: "voice",
    stage: "add_voice_success_criteria",
    source: "simulation_eval_drawer",
    artifactType: "voice_test",
    artifactId: String(testId),
    metadata: compactMetadata({
      test_id: testId,
      call_execution_id: callId,
      eval_config_id: evalConfig?.id,
      template_id: evalConfig?.template_id || evalConfig?.templateId,
      eval_name: evalConfig?.name,
    }),
    idempotencyKey: [
      "voice_success_criteria_added",
      testId,
      callId || "no-call",
      evalId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildVoiceMonitorOpenedPayload = ({
  testId,
  source,
  quickStartAttribution,
}) => ({
  eventName: "voice_call_monitor_opened",
  primaryPath: "voice",
  stage: "voice_monitor_calls",
  source,
  artifactType: "voice_test",
  artifactId: String(testId),
  metadata: compactMetadata({
    test_id: testId,
  }),
  idempotencyKey: ["voice_call_monitor_opened", testId].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});

export const buildVoiceOnboardingReturnHref = ({
  eventName = "voice_success_criteria_added",
  quickStartAttribution,
  search,
  ...attributionInput
} = {}) => {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("target_event", eventName);

  return appendVoiceOnboardingAttributionToHref(
    `/dashboard/home?${params.toString()}`,
    voiceQuickStartAttributionInput({
      quickStartAttribution:
        quickStartAttribution ||
        (Object.keys(attributionInput).length ? attributionInput : undefined),
      search,
    }),
  );
};
