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
  "voice",
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
    outcomePreview: option.outcomePreview,
    estimatedMinutes: option.estimatedMinutes,
    ...overrides,
  };
};

export const SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS = [
  quickStart("monitor_production_ai_app", {
    id: "observe",
    goal: "monitor_production_ai_app",
    surfaceLabel: "Observe",
    buttonLabel: "Connect your agent",
    shortDescription:
      "Choose your package, copy matching setup code, and turn one trace into a quality check.",
    firstActionLabel: "Choose package",
    pathPreview:
      "Choose package, copy setup code, send trace, review trace, create quality check.",
    sequencePreview: [
      "Choose package",
      "Copy setup code",
      "Send trace",
      "Review trace",
      "Create quality check",
    ],
    featured: true,
    icon: "mdi:connection",
  }),
  quickStart("test_and_improve_prompts", {
    id: "prompt",
    goal: "improve_prompts",
    surfaceLabel: "Prompts",
    buttonLabel: "Prove a prompt edit is better",
    shortDescription:
      "Test a prompt on real cases, lock a baseline, and see which edit wins.",
    firstActionLabel: "Write the prompt you want to improve",
    pathPreview:
      "Write the prompt, see how it scores, lock a baseline, try an edit, see which wins.",
    sequencePreview: [
      "Write the prompt you want to improve",
      "See how it scores on real cases",
      "Lock a baseline",
      "Try an edit and rerun",
      "See which edit wins",
    ],
    icon: "mdi:message-processing-outline",
  }),
  quickStart("build_or_prototype_agent", {
    id: "agent",
    goal: "build_ai_agent",
    surfaceLabel: "Agents",
    buttonLabel: "Watch your agent handle a hard call",
    shortDescription:
      "Run your agent on a real scenario, see where it failed, and lock in coverage.",
    firstActionLabel: "Stand up an agent you can run",
    pathPreview:
      "Stand up the agent, give it a prompt, run a scenario, see where it failed, add coverage.",
    sequencePreview: [
      "Stand up an agent you can run",
      "Give it a prompt and a model",
      "Watch your agent handle a real scenario",
      "See where it failed and why",
      "Catch that failure automatically",
    ],
    icon: "mdi:graph-outline",
  }),
  quickStart("route_llm_traffic_safely", {
    id: "gateway",
    goal: "control_model_traffic",
    surfaceLabel: "Gateway",
    buttonLabel: "Route LLM traffic safely",
    shortDescription:
      "Route your first request, see cost and latency per call, then add a guardrail.",
    firstActionLabel: "Route your first request",
    pathPreview:
      "Route the first request, get a key, see cost + latency, trace the log, add guardrails.",
    sequencePreview: [
      "Route your first request",
      "Get a key to route through",
      "See cost + latency per call",
      "Trace where time and spend went",
      "Put guardrails on future traffic",
    ],
    icon: "mdi:transit-connection-variant",
  }),
  quickStart("evaluate_quality", {
    id: "evals",
    goal: "evaluate_quality",
    surfaceLabel: "Simulation / Evals",
    buttonLabel: "Catch failing responses before users do",
    shortDescription:
      "Pick what to test, see which examples pass and fail, and find out exactly why.",
    firstActionLabel: "Pick what to test",
    pathPreview:
      "Pick what to test, define good, see pass/fail, see why each failed, fix the cause.",
    sequencePreview: [
      "Pick what to test",
      "Define what good looks like",
      "See which examples pass and fail",
      "See exactly why each one failed",
      "Fix the cause and rerun",
    ],
    icon: "mdi:check-decagram-outline",
  }),
  quickStart("connect_voice_ai_agent", {
    id: "voice",
    goal: "connect_voice_ai_agent",
    surfaceLabel: "Voice",
    buttonLabel: "Test a voice agent",
    shortDescription:
      "Hear how a call goes, see timing and interruptions, and set what a good call means.",
    firstActionLabel: "Bring in a voice agent to test",
    pathPreview:
      "Bring in the agent, hear a call, see timing + interruptions, define good, keep watching.",
    sequencePreview: [
      "Bring in a voice agent to test",
      "Hear how a call goes",
      "See timing, interruptions, and outcome",
      "Define what a good call sounds like",
      "Keep watching live calls",
    ],
    icon: "mdi:phone-in-talk-outline",
  }),
  quickStart("explore_sample_data", {
    id: SETUP_ORG_SAMPLE_PREVIEW_QUICK_START_ID,
    goal: "explore_sample_data",
    surfaceLabel: "Sample trace",
    buttonLabel: "Preview sample trace",
    previewButtonLabel: "Preview sample trace",
    shortDescription:
      "Use a preloaded trace to understand the product screens.",
    firstActionLabel: "Open sample trace",
    pathPreview: "Preview only. Real setup still starts from one product task.",
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
