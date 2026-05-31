import { ONBOARDING_GOAL_OPTIONS } from "src/sections/onboarding-home/onboarding-home.constants";

export const SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY =
  "futureagi.setup_quick_start_attribution";
export const SETUP_ORG_SAMPLE_PREVIEW_QUICK_START_ID = "sample_preview";
export const SETUP_ORG_FIRST_SETUP_QUICK_START_IDS = [
  "observe",
  "prompt",
  "agent",
  "gateway",
  "evals",
];

const goalOptionById = new Map(
  ONBOARDING_GOAL_OPTIONS.map((option) => [option.id, option]),
);

const quickStart = (goalId, overrides) => {
  const option = goalOptionById.get(goalId);
  if (!option) {
    throw new Error(`Unknown setup quick-start goal: ${goalId}`);
  }

  return {
    id: goalId,
    goal: overrides.goal || option.goal,
    goalLabel: option.label,
    primaryPath: option.primaryPath,
    label: option.label,
    description: option.description,
    estimatedMinutes: option.estimatedMinutes,
    ...overrides,
  };
};

export const SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS = [
  quickStart("monitor_production_ai_app", {
    id: "observe",
    goal: "monitor_production_ai_app",
    surfaceLabel: "Tracing",
    buttonLabel: "Connect your agent",
    shortDescription: "Connect traces from an AI app or agent.",
    firstActionLabel: "Create Observe project",
    pathPreview: "Send first trace, review first signal, add quality check.",
    featured: true,
    icon: "mdi:connection",
  }),
  quickStart("test_and_improve_prompts", {
    id: "prompt",
    goal: "improve_prompts",
    surfaceLabel: "Prompts",
    buttonLabel: "Test prompts or agent prompts",
    shortDescription: "Run prompt tests and compare output changes.",
    firstActionLabel: "Create prompt",
    pathPreview: "Run test, save version, compare versions, improve.",
    icon: "mdi:message-processing-outline",
  }),
  quickStart("build_or_prototype_agent", {
    id: "agent",
    goal: "build_ai_agent",
    surfaceLabel: "Agents",
    buttonLabel: "Prototype agent",
    shortDescription: "Run one scenario and inspect what the agent did.",
    firstActionLabel: "Create agent",
    pathPreview: "Run scenario, review run, save coverage.",
    icon: "mdi:graph-outline",
  }),
  quickStart("route_llm_traffic_safely", {
    id: "gateway",
    goal: "control_model_traffic",
    surfaceLabel: "Gateway",
    buttonLabel: "Set up gateway",
    shortDescription: "Add a provider, create a key, and send a request.",
    firstActionLabel: "Add provider",
    pathPreview: "Create key, send request, review log, add policy.",
    icon: "mdi:transit-connection-variant",
  }),
  quickStart("evaluate_quality", {
    id: "evals",
    goal: "evaluate_quality",
    surfaceLabel: "Evals / simulation",
    buttonLabel: "Test AI using simulation",
    shortDescription: "Create a small eval or simulation and review failures.",
    firstActionLabel: "Create dataset",
    pathPreview: "Add scorer, run eval, review failure, improve.",
    icon: "mdi:check-decagram-outline",
  }),
  quickStart("connect_voice_ai_agent", {
    id: "voice",
    goal: "connect_voice_ai_agent",
    surfaceLabel: "Voice",
    buttonLabel: "Connect voice agent",
    shortDescription: "Run a test call and review the transcript.",
    firstActionLabel: "Create agent",
    pathPreview: "Run call, review call, add success criteria.",
    icon: "mdi:phone-in-talk-outline",
  }),
  quickStart("explore_sample_data", {
    id: SETUP_ORG_SAMPLE_PREVIEW_QUICK_START_ID,
    goal: "explore_sample_data",
    surfaceLabel: "Sample screens",
    buttonLabel: "Open sample data",
    previewButtonLabel: "Preview sample screens",
    shortDescription: "Use preloaded data to understand the product screens.",
    firstActionLabel: "Open sample trace",
    pathPreview: "Preview only. Real setup still starts from a setup workflow.",
    icon: "mdi:chart-timeline-variant",
    sample: true,
  }),
];

export const isSetupOrgFirstSetupQuickStart = (option) =>
  SETUP_ORG_FIRST_SETUP_QUICK_START_IDS.includes(option?.id);

export const setupQuickStartAttributionFromId = (id) => {
  if (!id) return null;
  const option = SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.find(
    (quickStartOption) => quickStartOption.id === id,
  );

  if (!option) return null;
  return {
    quickStartGoal: option.goal,
    quickStartId: option.id,
    quickStartPrimaryPath: option.primaryPath,
  };
};

export const normalizeSetupQuickStartAttribution = ({
  quickStartGoal,
  quickStartId,
  quickStartPrimaryPath,
} = {}) => {
  const attribution = setupQuickStartAttributionFromId(quickStartId);
  if (!attribution) return {};
  if (quickStartGoal && quickStartGoal !== attribution.quickStartGoal) {
    return {};
  }
  if (
    quickStartPrimaryPath &&
    quickStartPrimaryPath !== attribution.quickStartPrimaryPath
  ) {
    return {};
  }
  return attribution;
};

const quickStartAttributionInput = (input = {}) => ({
  quickStartGoal: input.quickStartGoal ?? input.quick_start_goal,
  quickStartId: input.quickStartId ?? input.quick_start_id,
  quickStartPrimaryPath:
    input.quickStartPrimaryPath ?? input.quick_start_primary_path,
});

export const setupQuickStartAttributionParams = (input = {}) => {
  const attribution = normalizeSetupQuickStartAttribution(
    quickStartAttributionInput(input),
  );
  if (!attribution.quickStartId) return {};
  return {
    quick_start_goal: attribution.quickStartGoal,
    quick_start_id: attribution.quickStartId,
    quick_start_primary_path: attribution.quickStartPrimaryPath,
  };
};

export const appendSetupQuickStartAttributionToHref = (href, input = {}) => {
  const params = setupQuickStartAttributionParams(input);
  if (
    !href ||
    typeof href !== "string" ||
    !href.startsWith("/") ||
    href.startsWith("//") ||
    Object.keys(params).length === 0
  ) {
    return href;
  }

  const hashIndex = href.indexOf("#");
  const route = hashIndex >= 0 ? href.slice(0, hashIndex) : href;
  const hash = hashIndex >= 0 ? href.slice(hashIndex) : "";
  const queryIndex = route.indexOf("?");
  const pathname = queryIndex >= 0 ? route.slice(0, queryIndex) : route;
  const existingQuery = queryIndex >= 0 ? route.slice(queryIndex + 1) : "";
  const searchParams = new URLSearchParams(existingQuery);
  Object.entries(params).forEach(([key, value]) => {
    searchParams.set(key, value);
  });
  const query = searchParams.toString();

  return `${pathname}${query ? `?${query}` : ""}${hash}`;
};

const setupQuickStartStorage = () => {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage || null;
  } catch {
    return null;
  }
};

export const persistSetupQuickStartAttribution = (input = {}) => {
  const attribution = normalizeSetupQuickStartAttribution(input);

  try {
    const storage = setupQuickStartStorage();
    if (!attribution.quickStartId) {
      storage?.removeItem(SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY);
      return {};
    }

    storage?.setItem(
      SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY,
      JSON.stringify(attribution),
    );
  } catch {
    return attribution;
  }

  return attribution;
};

export const readPersistedSetupQuickStartAttribution = () => {
  const storage = setupQuickStartStorage();
  if (!storage) return {};

  try {
    const rawValue = storage.getItem(SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY);
    if (!rawValue) return {};
    const attribution = normalizeSetupQuickStartAttribution(
      JSON.parse(rawValue),
    );
    if (!attribution.quickStartId) {
      storage.removeItem(SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY);
      return {};
    }
    return attribution;
  } catch {
    storage.removeItem(SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY);
    return {};
  }
};
