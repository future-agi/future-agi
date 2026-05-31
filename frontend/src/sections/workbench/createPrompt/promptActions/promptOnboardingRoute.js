import { appendSetupQuickStartAttributionToHref } from "src/sections/auth/jwt/setup-org-quick-starts";

export const PROMPT_ONBOARDING_MODES = {
  CREATE_PROMPT: "create-prompt",
  RUN_TEST: "run-test",
  SAVE_VERSION: "save-version",
  COMPARE: "compare",
  ADD_FAILURE: "add-failure",
  METRICS: "metrics",
};

export const PROMPT_ONBOARDING_JOURNEY_STEPS = {
  CREATE_SECOND_VERSION: "create_second_prompt_version",
  COMPARE_VERSIONS: "compare_prompt_versions",
};

const VALID_PROMPT_ONBOARDING_MODES = new Set(
  Object.values(PROMPT_ONBOARDING_MODES),
);
const PROMPT_JOURNEY_STEP_MODES = {
  compare_prompt_versions: PROMPT_ONBOARDING_MODES.COMPARE,
  create_prompt: PROMPT_ONBOARDING_MODES.CREATE_PROMPT,
  create_second_prompt_version: PROMPT_ONBOARDING_MODES.COMPARE,
  prompt_next_loop: PROMPT_ONBOARDING_MODES.ADD_FAILURE,
  run_prompt_test: PROMPT_ONBOARDING_MODES.RUN_TEST,
  save_prompt_version: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
  start_prompt: PROMPT_ONBOARDING_MODES.CREATE_PROMPT,
};

const PROMPT_ONBOARDING_MODE_DESTINATIONS = {
  [PROMPT_ONBOARDING_MODES.CREATE_PROMPT]: {
    journeyStep: "start_prompt",
    tourAnchor: "prompt_create_button",
  },
  [PROMPT_ONBOARDING_MODES.RUN_TEST]: {
    journeyStep: "run_prompt_test",
    tourAnchor: "prompt_run_test_button",
  },
  [PROMPT_ONBOARDING_MODES.SAVE_VERSION]: {
    journeyStep: "save_prompt_version",
    tourAnchor: "prompt_save_version_button",
  },
  [PROMPT_ONBOARDING_MODES.COMPARE]: {
    journeyStep: PROMPT_ONBOARDING_JOURNEY_STEPS.COMPARE_VERSIONS,
    tourAnchor: "prompt_compare_versions_button",
  },
  [PROMPT_ONBOARDING_MODES.ADD_FAILURE]: {
    journeyStep: "prompt_next_loop",
    tab: "Evaluation",
    tourAnchor: "prompt_add_example_button",
  },
  [PROMPT_ONBOARDING_MODES.METRICS]: {
    tab: "Metrics",
  },
};

const PROMPT_ONBOARDING_JOURNEY_STEP_DESTINATIONS = {
  [PROMPT_ONBOARDING_JOURNEY_STEPS.CREATE_SECOND_VERSION]: {
    mode: PROMPT_ONBOARDING_MODES.COMPARE,
    journeyStep: PROMPT_ONBOARDING_JOURNEY_STEPS.CREATE_SECOND_VERSION,
    tourAnchor: "prompt_create_second_version_button",
  },
  [PROMPT_ONBOARDING_JOURNEY_STEPS.COMPARE_VERSIONS]: {
    mode: PROMPT_ONBOARDING_MODES.COMPARE,
    journeyStep: PROMPT_ONBOARDING_JOURNEY_STEPS.COMPARE_VERSIONS,
    tourAnchor: "prompt_compare_versions_button",
  },
};

const toSearchParams = (search = "") =>
  search instanceof URLSearchParams
    ? new URLSearchParams(search)
    : new URLSearchParams(search);

const setupQuickStartAttributionFromSearch = (search = "") => {
  const params = toSearchParams(search);
  return {
    quick_start_goal: params.get("quick_start_goal"),
    quick_start_id: params.get("quick_start_id"),
    quick_start_primary_path: params.get("quick_start_primary_path"),
  };
};

const setupQuickStartPayloadFromSearch = (search = "") => {
  const attribution = setupQuickStartAttributionFromSearch(search);
  return Object.entries({
    quickStartGoal: attribution.quick_start_goal,
    quickStartId: attribution.quick_start_id,
    quickStartPrimaryPath: attribution.quick_start_primary_path,
  }).reduce((result, [key, value]) => {
    if (value) result[key] = value;
    return result;
  }, {});
};

const safeKeyPart = (value, fallback) =>
  String(value || fallback)
    .replace(/[^a-zA-Z0-9_-]/g, "-")
    .slice(0, 56);

export const getPromptOnboardingRouteParams = (search = "") => {
  const params = toSearchParams(search);
  const rawMode = params.get("onboarding");
  const journeyStep = params.get("journey_step");
  const journeyMode = PROMPT_JOURNEY_STEP_MODES[journeyStep];
  const mode = VALID_PROMPT_ONBOARDING_MODES.has(rawMode)
    ? rawMode
    : journeyMode || null;
  const action = params.get("action") || journeyMode || null;

  return {
    action,
    isOnboarding: params.get("source") === "onboarding" || Boolean(mode),
    journeyStep,
    mode,
    tourAnchor: params.get("tour_anchor"),
  };
};

export const getSelectedPromptVersionsFromSearch = (search = "") => {
  const params = toSearchParams(search);
  const rawSelectedVersions = params.get("selected-versions");

  if (!rawSelectedVersions) return [];

  try {
    const selectedVersions = JSON.parse(rawSelectedVersions);
    return Array.isArray(selectedVersions) ? selectedVersions : [];
  } catch {
    return [];
  }
};

export const buildPromptEditorHref = ({
  journeyStep,
  mode,
  promptId,
  search,
  selectedVersions,
} = {}) => {
  if (!promptId) return null;

  const params = new URLSearchParams();
  params.set("source", "onboarding");
  if (Array.isArray(selectedVersions) && selectedVersions.length > 0) {
    params.set("selected-versions", JSON.stringify(selectedVersions));
  }
  if (VALID_PROMPT_ONBOARDING_MODES.has(mode)) {
    const destination =
      PROMPT_ONBOARDING_JOURNEY_STEP_DESTINATIONS[journeyStep] ||
      PROMPT_ONBOARDING_MODE_DESTINATIONS[mode];
    params.set("onboarding", destination?.mode || mode);
    if (destination?.tab) {
      params.set("tab", destination.tab);
    }
    if (destination?.tourAnchor) {
      params.set("tour_anchor", destination.tourAnchor);
    }
    if (destination?.journeyStep) {
      params.set("journey_step", destination.journeyStep);
    }
  }

  return appendSetupQuickStartAttributionToHref(
    `/dashboard/workbench/create/${promptId}?${params.toString()}`,
    setupQuickStartAttributionFromSearch(search),
  );
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

  return buildPromptEditorHref({ promptId, mode: nextMode, search });
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

export const isCommittedPromptVersion = (version = {}) => {
  if (!version) return false;

  const isDraft = version.isDraft ?? version.is_draft;
  const isDefault = version.isDefault ?? version.is_default;
  const commitMessage = version.commitMessage ?? version.commit_message;

  return Boolean(version) && !isDraft && Boolean(isDefault || commitMessage);
};

export const countCommittedPromptVersions = (versions = []) =>
  new Set(
    versions
      .filter(isCommittedPromptVersion)
      .map(
        (version) =>
          version.templateVersion ||
          version.template_version ||
          version.version ||
          version.id,
      )
      .filter(Boolean),
  ).size;

export const resolvePromptSaveCommitTarget = ({
  mode,
  selectedVersions = [],
  source,
} = {}) => {
  if (
    source === "onboarding" &&
    mode === PROMPT_ONBOARDING_MODES.SAVE_VERSION &&
    selectedVersions.length > 1
  ) {
    return (
      [...selectedVersions]
        .reverse()
        .find((version) => version?.isDraft ?? version?.is_draft) ||
      selectedVersions[selectedVersions.length - 1]
    );
  }

  return selectedVersions[0] || null;
};

const promptVersionKey = (version = {}) =>
  version?.version ||
  version?.templateVersion ||
  version?.template_version ||
  version?.id ||
  null;

export const resolvePromptPostSaveJourneyStep = ({
  baseVersion,
  commitTarget,
} = {}) => {
  const baseKey = promptVersionKey(baseVersion);
  const targetKey = promptVersionKey(commitTarget);
  const targetsAdditionalVersion =
    baseKey && targetKey
      ? baseKey !== targetKey
      : Boolean(commitTarget && commitTarget !== baseVersion);

  return targetsAdditionalVersion
    ? PROMPT_ONBOARDING_JOURNEY_STEPS.COMPARE_VERSIONS
    : PROMPT_ONBOARDING_JOURNEY_STEPS.CREATE_SECOND_VERSION;
};

export const shouldAdvancePromptCompareOnboarding = ({
  committedVersionCount,
  mode,
  selectedVersionCount,
  source,
} = {}) =>
  source === "onboarding" &&
  mode === PROMPT_ONBOARDING_MODES.COMPARE &&
  selectedVersionCount > 1 &&
  committedVersionCount > 1;

export const isPromptFailureCaptureOnboarding = ({ mode, source } = {}) =>
  source === "onboarding" && mode === PROMPT_ONBOARDING_MODES.ADD_FAILURE;

export const buildPromptCreatedPayload = ({ promptId, search } = {}) => {
  const safePromptId = safeKeyPart(promptId, "prompt");

  return {
    eventName: "prompt_created",
    primaryPath: "prompt",
    stage: "start_prompt",
    source: "prompt_template",
    metadata: {
      step: PROMPT_ONBOARDING_MODES.CREATE_PROMPT,
      template_id: promptId,
    },
    ...setupQuickStartPayloadFromSearch(search),
    idempotencyKey: ["prompt_onboarding", "prompt_created", safePromptId]
      .filter(Boolean)
      .join(":"),
  };
};

export const buildPromptTestRunCompletedPayload = ({
  promptId,
  search,
  versions = [],
} = {}) => {
  const safePromptId = safeKeyPart(promptId, "prompt");
  const safeVersions = versions.map((version, index) =>
    safeKeyPart(promptVersionKey(version), `version-${index + 1}`),
  );

  return {
    eventName: "prompt_test_run_completed",
    primaryPath: "prompt",
    stage: "run_prompt_test",
    source: "prompt_playground",
    metadata: {
      step: PROMPT_ONBOARDING_MODES.RUN_TEST,
      template_id: promptId,
      version_count: versions.length,
    },
    ...setupQuickStartPayloadFromSearch(search),
    idempotencyKey: [
      "prompt_onboarding",
      "prompt_test_run_completed",
      safePromptId,
      safeVersions.join("-"),
    ]
      .filter(Boolean)
      .join(":"),
  };
};

export const buildPromptVersionCreatedPayload = ({
  promptId,
  search,
  version,
} = {}) => {
  const safePromptId = safeKeyPart(promptId, "prompt");
  const versionKey = promptVersionKey(version);
  const safeVersion = safeKeyPart(versionKey, "version");

  return {
    eventName: "prompt_version_created",
    primaryPath: "prompt",
    stage: "save_prompt_version",
    source: "prompt_template",
    metadata: {
      step: PROMPT_ONBOARDING_MODES.SAVE_VERSION,
      template_id: promptId,
      version: versionKey,
    },
    ...setupQuickStartPayloadFromSearch(search),
    idempotencyKey: [
      "prompt_onboarding",
      "prompt_version_created",
      safePromptId,
      safeVersion,
    ]
      .filter(Boolean)
      .join(":"),
  };
};

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
