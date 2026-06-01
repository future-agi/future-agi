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
    description: "Inspect real traces and outcomes.",
  },
  {
    id: "ship",
    label: "Ship",
    description: "Move the workflow toward production.",
  },
  {
    id: "improve",
    label: "Improve",
    description: "Turn findings into a repeatable guardrail.",
  },
];

export const ONBOARDING_GOAL_OPTIONS = [
  {
    id: "monitor_production_ai_app",
    goal: "monitor_production_ai_app",
    primaryPath: "observe",
    label: "Connect your agent",
    description: "Connect traces from an AI app or agent.",
    outcomePreview: "A real trace reviewed and a quality check ready to add.",
    estimatedMinutes: 5,
  },
  {
    id: "test_and_improve_prompts",
    goal: "test_and_improve_prompts",
    primaryPath: "prompt",
    label: "Test prompts or agent prompts",
    description: "Run prompt tests and compare output changes.",
    outcomePreview: "A tested prompt version with a comparison baseline.",
    estimatedMinutes: 6,
  },
  {
    id: "build_or_prototype_agent",
    goal: "build_or_prototype_agent",
    primaryPath: "agent",
    label: "Prototype agent",
    description: "Run a first scenario and inspect the agent trace.",
    outcomePreview: "A scenario run you can inspect and turn into coverage.",
    estimatedMinutes: 8,
  },
  {
    id: "route_llm_traffic_safely",
    goal: "route_llm_traffic_safely",
    primaryPath: "gateway",
    label: "Set up gateway",
    description: "Add a provider, create a key, and send a gateway request.",
    outcomePreview: "A gateway request log ready for routing or policy review.",
    estimatedMinutes: 7,
  },
  {
    id: "evaluate_quality",
    goal: "evaluate_quality",
    primaryPath: "evals",
    label: "Test AI with Simulation / Evals",
    description:
      "Choose a source, run a quality check, and fix or finish from the first result.",
    outcomePreview:
      "A reviewed quality result with the right next fix or completion path.",
    estimatedMinutes: 7,
  },
  {
    id: "connect_voice_ai_agent",
    goal: "connect_voice_ai_agent",
    primaryPath: "voice",
    label: "Connect a voice AI agent",
    description: "Run or review a call with clear success criteria.",
    outcomePreview: "A test call transcript with success criteria to add.",
    estimatedMinutes: 8,
  },
  {
    id: "explore_sample_data",
    goal: "explore_sample_data",
    primaryPath: "sample",
    label: "Preview sample trace",
    description: "Inspect a sample trace while real setup is pending.",
    outcomePreview: "A sample trace reviewed before connecting real data.",
    estimatedMinutes: 2,
  },
];

export const ONBOARDING_PATH_LABELS = {
  agent: "Prototype agent",
  evals: "Test AI with Simulation / Evals",
  gateway: "Set up gateway",
  observe: "Connect your agent",
  prompt: "Test prompts or agent prompts",
  sample: "Preview sample trace",
  voice: "Connect a voice AI agent",
};

export const ONBOARDING_EVENT_LABELS = {
  agent_created: "Agent created",
  agent_eval_created: "Agent evaluator created",
  agent_node_added: "Agent step added",
  agent_scenario_saved_as_eval: "Agent scenario saved as evaluator",
  agent_trace_reviewed: "Agent trace reviewed",
  daily_quality_action_completed: "Daily quality action completed",
  daily_quality_action_opened: "Daily quality action opened",
  daily_quality_item_reviewed: "Daily quality item reviewed",
  dataset_example_added: "Dataset example added",
  eval_dataset_created: "Quality source created",
  eval_failure_action_created: "Eval fix action created",
  eval_failures_reviewed: "Eval result reviewed",
  eval_run_completed: "Quality check run completed",
  eval_scorer_created: "Quality check created",
  first_quality_loop_completed: "First product workflow completed",
  gateway_failure_resolved: "Gateway failure resolved",
  gateway_key_created: "Gateway key created",
  gateway_log_opened: "Gateway log opened",
  gateway_policy_created: "Gateway policy created",
  gateway_provider_added: "Gateway provider added",
  gateway_request_seen: "Gateway request received",
  observe_project_created: "Observe project created",
  onboarding_goal_selected: "Setup task selected",
  onboarding_recommended_action_clicked: "Setup action opened",
  prompt_comparable_version_created: "Comparable prompt version created",
  prompt_created: "Prompt created",
  prompt_test_run_completed: "Prompt test run completed",
  prompt_version_created: "Prompt version saved",
  sample_signal_viewed: "Sample trace previewed",
  trace_detail_opened: "Trace detail opened",
  trace_received: "Trace received",
  trace_reviewed: "Trace reviewed",
  voice_agent_created: "Voice agent connected",
  voice_call_monitor_opened: "Voice call monitor opened",
  voice_call_reviewed: "Voice call reviewed",
  voice_success_criteria_added: "Voice success criteria added",
  voice_test_call_completed: "Voice test call completed",
};

const isPreviewOnlyGoal = (goal) =>
  goal?.primaryPath === "sample" || goal?.goal === "explore_sample_data";

export const ONBOARDING_STAGE_COPY = {
  feature_disabled: {
    eyebrow: "Setup",
    title: "Continue product setup",
    description: "Product setup is available for this workspace.",
  },
  workspace_missing: {
    eyebrow: "Workspace",
    title: "Choose a workspace",
    description: "Select a workspace before starting setup.",
  },
  permission_limited: {
    eyebrow: "Access",
    title: "Request access for setup",
    description: "This workspace needs write access before setup can continue.",
  },
  choose_goal: {
    eyebrow: "Setup",
    title: "Choose what to set up first",
    description:
      "Pick one product area. Home will open the first setup step and keep the next step ready.",
  },
  selected_path_unavailable: {
    eyebrow: "Path unavailable",
    title: "Start with an available path",
    description: "This setup option is not available in the product yet.",
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
  create_second_prompt_version: {
    eyebrow: "Prompt version",
    title: "Create a second version",
    description: "Edit, run, and save one more version before comparing.",
  },
  compare_prompt_versions: {
    eyebrow: "Prompt compare",
    title: "Compare prompt versions",
    description: "Compare the baseline against the edited version.",
  },
  prompt_next_loop: {
    eyebrow: "Prompt setup",
    title: "Continue prompt setup",
    description: "Turn the comparison into the next reusable eval.",
  },
  create_agent: {
    eyebrow: "Agent",
    title: "Create an agent",
    description: "Create the first agent before running a scenario.",
  },
  add_agent_node: {
    eyebrow: "Agent step",
    title: "Add a starter prompt",
    description: "Add a runnable prompt with a model and sample input.",
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
    title: "Create the first agent eval",
    description: "Create the eval that keeps the agent behavior measurable.",
  },
  configure_gateway_provider: {
    eyebrow: "Gateway",
    title: "Configure a model provider",
    description: "Add a provider before routing model traffic.",
  },
  create_gateway_key: {
    eyebrow: "Gateway key",
    title: "Create a gateway key",
    description: "Create a key for the first routed request.",
  },
  run_gateway_request: {
    eyebrow: "Gateway request",
    title: "Run a gateway request",
    description: "Send one request and inspect the first log.",
  },
  review_gateway_log: {
    eyebrow: "Gateway log",
    title: "Review the gateway log",
    description: "Inspect the first log for cost, latency, and failures.",
  },
  fix_gateway_failure: {
    eyebrow: "Gateway quality",
    title: "Fix the first gateway failure",
    description: "Turn the first failure into a routing or provider change.",
  },
  add_gateway_policy: {
    eyebrow: "Gateway policy",
    title: "Add a gateway policy",
    description: "Add a policy that keeps the request path controlled.",
  },
  create_eval_dataset: {
    eyebrow: "Simulation / Evals",
    title: "Choose what to test",
    description:
      "Choose a focused dataset, simulation, or trace source before adding the quality check.",
  },
  add_eval_scorer: {
    eyebrow: "Quality check",
    title: "Add the quality check",
    description: "Define what good output looks like before running it.",
  },
  run_eval: {
    eyebrow: "Quality run",
    title: "Run the first quality check",
    description: "Run it once so the first result is reviewable.",
  },
  review_eval_failures: {
    eyebrow: "Result review",
    title: "Review the first result",
    description:
      "Inspect the result and choose whether to fix the source or tune the quality check.",
  },
  eval_next_loop: {
    eyebrow: "Fix or finish",
    title: "Fix from the result",
    description: "Turn the reviewed failure into a source fix, then rerun.",
  },
  create_voice_agent: {
    eyebrow: "Voice",
    title: "Create a voice agent",
    description:
      "Create or connect one voice agent before running a test call.",
  },
  run_voice_test_call: {
    eyebrow: "Voice test call",
    title: "Run a voice test call",
    description: "Run one call so the transcript and outcome are reviewable.",
  },
  review_voice_call: {
    eyebrow: "Voice call",
    title: "Review the voice call",
    description:
      "Inspect the call transcript and identify the first improvement.",
  },
  add_voice_success_criteria: {
    eyebrow: "Voice criteria",
    title: "Add voice success criteria",
    description: "Define what a good call should satisfy before monitoring.",
  },
  voice_monitor_calls: {
    eyebrow: "Voice monitor",
    title: "Monitor voice calls",
    description: "Review live calls against the saved criteria.",
  },
  connect_observability: {
    eyebrow: "Observe",
    title: "Connect your agent",
    description:
      "Choose your package, create an Observe project, and send one trace.",
  },
  waiting_for_first_trace: {
    eyebrow: "Waiting",
    title: "Waiting for the first trace",
    description:
      "Keep the trace page open, run one request, and Future AGI will open the trace when it appears.",
  },
  waiting_for_first_trace_sample_available: {
    eyebrow: "Waiting",
    title: "Waiting for real data",
    description: "Use a sample trace while the first real trace is pending.",
  },
  review_first_trace: {
    eyebrow: "First trace",
    title: "First trace received",
    description:
      "Review the trace to inspect inputs, outputs, latency, cost, and errors.",
  },
  create_trace_evaluator: {
    eyebrow: "Quality check",
    title: "Create a quality check",
    description: "Turn the reviewed trace into a repeatable check.",
  },
  activated: {
    eyebrow: "First setup complete",
    title: "Your first workflow is live",
    description:
      "Review daily quality next and keep improving the workflow from real traces.",
  },
  daily_review: {
    eyebrow: "Daily quality",
    title: "Review today's quality item",
    description: "Open the latest item and keep the loop fresh.",
  },
  review_sample_signal: {
    eyebrow: "Sample data",
    title: "Review a sample trace",
    description:
      "Inspect the sample trace while real workspace data is pending.",
  },
};

export const getStageCopy = (state) =>
  state?.stageCopy ||
  ONBOARDING_STAGE_COPY[state?.stage] || {
    eyebrow: "Setup",
    title: "Continue product setup",
    description: "Create an Observe project, send one trace, and review it.",
  };

export const readableToken = (value) =>
  value ? String(value).replaceAll("_", " ") : "not set";

export const readablePath = (value) =>
  ONBOARDING_PATH_LABELS[value] || readableToken(value);

export const readableEvent = (value) =>
  ONBOARDING_EVENT_LABELS[value] || readableToken(value);

const optionFromResponse = (goal) => ({
  id: goal.id || goal.goal,
  goal: goal.goal,
  primaryPath: goal.primary_path || goal.primaryPath,
  label: goal.label,
  description: goal.description,
  outcomePreview: goal.outcome_preview || goal.outcomePreview || null,
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

  return goals
    .filter((goal) => !isPreviewOnlyGoal(goal))
    .map((goal) => {
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
