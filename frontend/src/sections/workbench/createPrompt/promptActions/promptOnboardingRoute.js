export const PROMPT_ONBOARDING_MODES = {
  CREATE_PROMPT: "create-prompt",
  RUN_TEST: "run-test",
  SAVE_VERSION: "save-version",
  COMPARE: "compare",
  ADD_FAILURE: "add-failure",
  METRICS: "metrics",
};

const VALID_PROMPT_ONBOARDING_MODES = new Set(
  Object.values(PROMPT_ONBOARDING_MODES),
);
const PROMPT_JOURNEY_STEP_MODES = {
  compare_prompt_versions: PROMPT_ONBOARDING_MODES.COMPARE,
  create_prompt: PROMPT_ONBOARDING_MODES.CREATE_PROMPT,
  prompt_next_loop: PROMPT_ONBOARDING_MODES.ADD_FAILURE,
  run_prompt_test: PROMPT_ONBOARDING_MODES.RUN_TEST,
  save_prompt_version: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
  start_prompt: PROMPT_ONBOARDING_MODES.CREATE_PROMPT,
};

const toSearchParams = (search = "") =>
  search instanceof URLSearchParams
    ? new URLSearchParams(search)
    : new URLSearchParams(search);

const safeKeyPart = (value, fallback) =>
  String(value || fallback)
    .replace(/[^a-zA-Z0-9_-]/g, "-")
    .slice(0, 56);

export const getPromptOnboardingRouteParams = (search = "") => {
  const params = toSearchParams(search);
  const rawMode = params.get("onboarding");
  const journeyMode = PROMPT_JOURNEY_STEP_MODES[params.get("journey_step")];
  const mode = VALID_PROMPT_ONBOARDING_MODES.has(rawMode)
    ? rawMode
    : journeyMode || null;
  const action = params.get("action") || journeyMode || null;

  return {
    action,
    isOnboarding: params.get("source") === "onboarding" || Boolean(mode),
    mode,
  };
};

export const buildPromptEditorHref = ({ mode, promptId } = {}) => {
  if (!promptId) return null;

  const params = new URLSearchParams();
  params.set("source", "onboarding");
  if (VALID_PROMPT_ONBOARDING_MODES.has(mode)) {
    params.set("onboarding", mode);
  }

  return `/dashboard/workbench/create/${promptId}?${params.toString()}`;
};

export const buildPromptCreatedHref = ({ promptId, search } = {}) => {
  if (!promptId) return "/dashboard/workbench/all";

  const { action, isOnboarding, mode } = getPromptOnboardingRouteParams(search);
  if (!isOnboarding && !mode) {
    return `/dashboard/workbench/create/${promptId}`;
  }

  const nextMode =
    action === PROMPT_ONBOARDING_MODES.CREATE_PROMPT ||
    mode === PROMPT_ONBOARDING_MODES.CREATE_PROMPT ||
    isOnboarding
      ? PROMPT_ONBOARDING_MODES.RUN_TEST
      : mode;

  return buildPromptEditorHref({ promptId, mode: nextMode });
};

export const shouldAdvancePromptRunOnboarding = ({
  isContentEmpty,
  isGenerating,
  loadingPrompt,
  mode,
  source,
} = {}) =>
  source === "onboarding" &&
  mode === PROMPT_ONBOARDING_MODES.RUN_TEST &&
  !loadingPrompt &&
  !isGenerating &&
  !isContentEmpty;

export const shouldAdvancePromptSaveOnboarding = ({ mode, source } = {}) =>
  source === "onboarding" && mode === PROMPT_ONBOARDING_MODES.SAVE_VERSION;

export const shouldAdvancePromptCompareOnboarding = ({
  mode,
  selectedVersionCount,
  source,
} = {}) =>
  source === "onboarding" &&
  mode === PROMPT_ONBOARDING_MODES.COMPARE &&
  selectedVersionCount > 1;

export const isPromptFailureCaptureOnboarding = ({ mode, source } = {}) =>
  source === "onboarding" && mode === PROMPT_ONBOARDING_MODES.ADD_FAILURE;

export const buildPromptComparisonCompletedPayload = ({
  promptId,
  versions = [],
} = {}) => {
  const safePromptId = safeKeyPart(promptId, "prompt");
  const safeVersions = versions.map((version, index) =>
    safeKeyPart(version, `version-${index + 1}`),
  );

  return {
    eventName: "prompt_comparison_completed",
    primaryPath: "prompt",
    stage: "compare_prompt_versions",
    source: "prompt_template",
    metadata: {
      step: PROMPT_ONBOARDING_MODES.COMPARE,
      template_id: promptId,
      version_count: versions.length,
    },
    idempotencyKey: [
      "prompt_onboarding",
      "prompt_comparison_completed",
      safePromptId,
      safeVersions.join("-"),
    ]
      .filter(Boolean)
      .join(":"),
  };
};

export const buildPromptFirstQualityLoopCompletedPayload = ({
  promptId,
} = {}) => {
  const safePromptId = safeKeyPart(promptId, "prompt");

  return {
    eventName: "first_quality_loop_completed",
    primaryPath: "prompt",
    stage: "activated",
    source: "prompt_metrics",
    metadata: {
      step: PROMPT_ONBOARDING_MODES.METRICS,
      template_id: promptId,
    },
    idempotencyKey: [
      "prompt_onboarding",
      "first_quality_loop_completed",
      safePromptId,
    ]
      .filter(Boolean)
      .join(":"),
  };
};
