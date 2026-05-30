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

export const getVoiceOnboardingParams = (search = "") => {
  const params = new URLSearchParams(search);
  const journeyMode = MODE_BY_JOURNEY_STEP[params.get("journey_step")] || "";
  return {
    mode: params.get("onboarding") || journeyMode,
    from: params.get("from") || (journeyMode ? "onboarding" : ""),
    callId: params.get("call_id") || "",
    agentDefinitionId: params.get("agent_definition_id") || "",
  };
};

export const isVoiceOnboardingMode = (mode) =>
  Object.values(VOICE_ONBOARDING_MODES).includes(mode);

export const buildVoiceRouteFocusPayload = ({
  mode,
  source,
  testId,
  executionId,
  callId,
  agentDefinitionId,
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
  };
};

export const buildVoiceCallReviewedPayload = ({
  testId,
  executionId,
  callId,
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
});

export const buildVoiceSuccessCriteriaAddedPayload = ({
  testId,
  callId,
  evalConfig,
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
  };
};

export const buildVoiceMonitorOpenedPayload = ({ testId, source }) => ({
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
});

export const buildVoiceOnboardingReturnHref = ({
  eventName = "voice_success_criteria_added",
} = {}) => {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("target_event", eventName);

  return `/dashboard/home?${params.toString()}`;
};
