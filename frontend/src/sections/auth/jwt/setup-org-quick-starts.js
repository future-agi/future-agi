import { ONBOARDING_GOAL_OPTIONS } from "src/sections/onboarding-home/onboarding-home.constants";

export const SETUP_QUICK_START_ATTRIBUTION_STORAGE_KEY =
  "futureagi.setup_quick_start_attribution";

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
  quickStart("explore_sample_data", {
    id: "sample_preview",
    goal: "explore_sample_data",
    buttonLabel: "Preview sample trace first",
    shortDescription: "See a quality signal immediately.",
    icon: "mdi:chart-timeline-variant",
    featured: true,
  }),
  quickStart("monitor_production_ai_app", {
    id: "observe",
    goal: "monitor_production_ai_app",
    buttonLabel: "Connect observability first",
    shortDescription: "Send a trace and review the first signal.",
    icon: "mdi:radar",
    featured: true,
  }),
  quickStart("test_and_improve_prompts", {
    id: "prompt",
    goal: "improve_prompts",
    buttonLabel: "Test prompts",
    shortDescription: "Create, run, and version one prompt.",
    icon: "mdi:message-processing-outline",
  }),
  quickStart("build_or_prototype_agent", {
    id: "agent",
    goal: "build_ai_agent",
    buttonLabel: "Prototype agent",
    shortDescription: "Run one scenario and inspect the trace.",
    icon: "mdi:graph-outline",
  }),
  quickStart("route_llm_traffic_safely", {
    id: "gateway",
    goal: "control_model_traffic",
    buttonLabel: "Route gateway",
    shortDescription: "Send one model request safely.",
    icon: "mdi:transit-connection-variant",
  }),
  quickStart("evaluate_quality", {
    id: "evals",
    goal: "evaluate_quality",
    buttonLabel: "Run eval",
    shortDescription: "Create a small eval and review failures.",
    icon: "mdi:check-decagram-outline",
  }),
  quickStart("connect_voice_ai_agent", {
    id: "voice",
    goal: "connect_voice_ai_agent",
    buttonLabel: "Connect voice",
    shortDescription: "Review a call quality loop.",
    icon: "mdi:phone-in-talk-outline",
  }),
];

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
