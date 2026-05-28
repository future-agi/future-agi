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

const toSearchParams = (search = "") =>
  search instanceof URLSearchParams
    ? new URLSearchParams(search)
    : new URLSearchParams(search);

export const getPromptOnboardingRouteParams = (search = "") => {
  const params = toSearchParams(search);
  const mode = params.get("onboarding");

  return {
    action: params.get("action"),
    isOnboarding: params.get("source") === "onboarding",
    mode: VALID_PROMPT_ONBOARDING_MODES.has(mode) ? mode : null,
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
