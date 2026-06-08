import { setupQuickStartAttributionParams } from "src/sections/auth/jwt/setup-org-quick-starts";

export const TEST_ONBOARDING_MODES = {
  CREATE_EVAL: "create-eval",
  SAVE_EVAL: "save-eval",
};

export const isEvalOnboardingMode = (mode) =>
  mode === TEST_ONBOARDING_MODES.CREATE_EVAL ||
  mode === TEST_ONBOARDING_MODES.SAVE_EVAL;

const safeKeyPart = (value, fallback) =>
  String(value || fallback)
    .replace(/[^a-zA-Z0-9_-]/g, "-")
    .slice(0, 56);

const compactObject = (value = {}) =>
  Object.fromEntries(
    Object.entries(value).filter(
      ([, item]) => item !== undefined && item !== null && item !== "",
    ),
  );

export const buildAgentEvalCoveragePayload = ({
  evalConfig,
  executionIds = [],
  mode,
  quickStartAttribution,
  testId,
} = {}) => {
  if (!testId || !isEvalOnboardingMode(mode)) {
    return null;
  }

  const eventName =
    mode === TEST_ONBOARDING_MODES.CREATE_EVAL
      ? "agent_eval_created"
      : "agent_scenario_saved_as_eval";
  const stage =
    mode === TEST_ONBOARDING_MODES.CREATE_EVAL
      ? "agent_create_eval"
      : "save_agent_eval";
  const evalConfigId =
    evalConfig?.id ?? evalConfig?.eval_config_id ?? evalConfig?.evalConfigId;
  const evalTemplateId =
    evalConfig?.template_id ?? evalConfig?.templateId ?? evalConfig?.template;
  const safeTestId = safeKeyPart(testId, "test");
  const safeEvalPart = safeKeyPart(
    evalConfigId || evalTemplateId || mode,
    "eval",
  );

  return compactObject({
    eventName,
    primaryPath: "agent",
    stage,
    source: "simulate",
    artifactType: evalConfigId ? "eval" : undefined,
    artifactId: evalConfigId,
    metadata: compactObject({
      step: mode,
      test_id: testId,
      eval_config_id: evalConfigId,
      eval_template_id: evalTemplateId,
      execution_count: executionIds.length,
    }),
    idempotencyKey: [
      "agent_onboarding",
      eventName,
      safeTestId,
      safeEvalPart,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  });
};
