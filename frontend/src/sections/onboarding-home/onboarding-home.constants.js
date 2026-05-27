export const ONBOARDING_PRODUCT_LOOP_STEPS = [
  {
    id: "build",
    label: "Build",
    description: "Create or connect the AI surface.",
  },
  {
    id: "test",
    label: "Test",
    description: "Run a focused check before shipping.",
  },
  {
    id: "observe",
    label: "Observe",
    description: "Inspect real quality signals.",
  },
  {
    id: "ship",
    label: "Ship",
    description: "Move the workflow toward production.",
  },
  {
    id: "improve",
    label: "Improve",
    description: "Turn signal into a repeatable guardrail.",
  },
];

export const ONBOARDING_GOAL_OPTIONS = [
  {
    id: "monitor_production_ai_app",
    goal: "monitor_production_ai_app",
    primaryPath: "observe",
    label: "Monitor a production AI app",
    description: "Connect traces and review the first quality signal.",
    estimatedMinutes: 5,
  },
  {
    id: "test_and_improve_prompts",
    goal: "test_and_improve_prompts",
    primaryPath: "prompt",
    label: "Test and improve prompts",
    description: "Create a prompt test loop and compare output changes.",
    estimatedMinutes: 6,
  },
  {
    id: "build_or_prototype_agent",
    goal: "build_or_prototype_agent",
    primaryPath: "agent",
    label: "Build or prototype an AI agent",
    description: "Run a first scenario and inspect the agent trace.",
    estimatedMinutes: 8,
  },
  {
    id: "route_llm_traffic_safely",
    goal: "route_llm_traffic_safely",
    primaryPath: "gateway",
    label: "Route LLM traffic safely",
    description: "Send a gateway request and review the first log.",
    estimatedMinutes: 7,
  },
  {
    id: "evaluate_quality",
    goal: "evaluate_quality",
    primaryPath: "evals",
    label: "Evaluate quality on data or traces",
    description: "Create a small eval and review the first failure.",
    estimatedMinutes: 7,
  },
  {
    id: "connect_voice_ai_agent",
    goal: "connect_voice_ai_agent",
    primaryPath: "voice",
    label: "Connect a voice AI agent",
    description: "Run or review a call with clear success criteria.",
    estimatedMinutes: 8,
  },
  {
    id: "explore_sample_data",
    goal: "explore_sample_data",
    primaryPath: "sample",
    label: "Explore with sample data",
    description: "Review sample signals while real data is pending.",
    estimatedMinutes: 2,
  },
];

export const ONBOARDING_STAGE_COPY = {
  feature_disabled: {
    eyebrow: "Setup",
    title: "Start with the setup checklist",
    description: "The existing checklist is available for this workspace.",
  },
  workspace_missing: {
    eyebrow: "Workspace",
    title: "Choose a workspace",
    description: "Select a workspace before starting a product loop.",
  },
  permission_limited: {
    eyebrow: "Access",
    title: "Request access for setup",
    description: "This workspace needs write access before setup can continue.",
  },
  choose_goal: {
    eyebrow: "First goal",
    title: "Choose what to set up first",
    description: "Pick the first product job before moving into setup.",
  },
  selected_path_unavailable: {
    eyebrow: "Path unavailable",
    title: "Start with an available path",
    description: "This selected path is not available in the product yet.",
  },
  start_prompt: {
    eyebrow: "Prompt",
    title: "Start a prompt loop",
    description: "Create one prompt and run it against a focused example.",
  },
  run_prompt_test: {
    eyebrow: "Prompt test",
    title: "Run a prompt test",
    description: "Run the prompt once before saving a baseline version.",
  },
  save_prompt_version: {
    eyebrow: "Prompt version",
    title: "Save the prompt version",
    description: "Save the tested version so the next edit has a baseline.",
  },
  compare_prompt_versions: {
    eyebrow: "Prompt compare",
    title: "Compare prompt versions",
    description: "Compare the baseline against the edited version.",
  },
  prompt_next_loop: {
    eyebrow: "Prompt loop",
    title: "Continue the prompt loop",
    description: "Turn the comparison into the next reusable quality check.",
  },
  create_agent: {
    eyebrow: "Agent",
    title: "Create an agent",
    description: "Create the first agent before running a scenario.",
  },
  run_agent_scenario: {
    eyebrow: "Agent scenario",
    title: "Run a scenario",
    description: "Run one scenario and inspect what the agent did.",
  },
  review_agent_trace: {
    eyebrow: "Agent trace",
    title: "Review the agent trace",
    description: "Inspect the trace and identify the first improvement.",
  },
  save_agent_eval: {
    eyebrow: "Agent eval",
    title: "Save an agent eval",
    description: "Convert the observed behavior into a repeatable eval.",
  },
  agent_create_eval: {
    eyebrow: "Agent quality",
    title: "Create the first agent quality check",
    description: "Create the check that keeps the agent behavior measurable.",
  },
  connect_observability: {
    eyebrow: "Observe",
    title: "Connect observability",
    description: "Create an observe project and send one trace.",
  },
  waiting_for_first_trace: {
    eyebrow: "Waiting",
    title: "Waiting for the first trace",
    description: "Once a trace lands, the next review action will unlock.",
  },
  waiting_for_first_trace_sample_available: {
    eyebrow: "Waiting",
    title: "Waiting for real data",
    description: "Use a sample signal while the first real trace is pending.",
  },
  review_first_trace: {
    eyebrow: "First signal",
    title: "Review the first trace",
    description:
      "Inspect the first trace and capture the first quality signal.",
  },
  create_trace_evaluator: {
    eyebrow: "Quality loop",
    title: "Create an evaluator",
    description: "Turn the reviewed trace into a repeatable quality check.",
  },
  activated: {
    eyebrow: "Activated",
    title: "First quality loop is ready",
    description: "The workspace has completed a first meaningful loop.",
  },
  daily_review: {
    eyebrow: "Daily quality",
    title: "Review today's quality signal",
    description: "Open the latest quality signal and keep the loop fresh.",
  },
  review_sample_signal: {
    eyebrow: "Sample data",
    title: "Review a sample signal",
    description:
      "Inspect the sample signal while real workspace data is pending.",
  },
};

export const getStageCopy = (state) =>
  state?.stageCopy ||
  ONBOARDING_STAGE_COPY[state?.stage] || {
    eyebrow: "Setup",
    title: "Open Get Started",
    description: "The existing setup checklist is available.",
  };

export const readableToken = (value) =>
  value ? String(value).replaceAll("_", " ") : "not set";

const optionFromResponse = (goal) => ({
  id: goal.id || goal.goal,
  goal: goal.goal,
  primaryPath: goal.primary_path || goal.primaryPath,
  label: goal.label,
  description: goal.description,
  estimatedMinutes: goal.estimated_minutes || goal.estimatedMinutes || null,
  disabled: Boolean(goal.disabled),
  disabledReason: goal.disabled_reason || goal.disabledReason || null,
});

export const getGoalOptionsForState = (state) => {
  const goals = Array.isArray(state?.availableGoals)
    ? state.availableGoals.map(optionFromResponse)
    : ONBOARDING_GOAL_OPTIONS;
  const availablePaths = new Map(
    (state?.availablePaths || []).map((path) => [path.id, path]),
  );

  return goals.map((goal) => {
    const path = availablePaths.get(goal.primaryPath);
    const route = state?.routeAvailability?.[`path_${goal.primaryPath}`];
    const hasPathEvidence = Boolean(path || route);
    const isAvailable =
      goal.disabled !== true &&
      (path?.isAvailable ?? route?.isAvailable ?? !hasPathEvidence);
    const disabledReason =
      goal.disabledReason ||
      path?.blockedReason ||
      route?.reason ||
      (isAvailable ? null : "path unavailable");

    return {
      ...goal,
      href: path?.href || route?.href || "",
      isAvailable,
      disabled: !isAvailable,
      disabledReason,
    };
  });
};
