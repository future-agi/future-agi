export const PATH_FOCUS_PLANS = {
  prompt: {
    eyebrow: "Prompt loop",
    title: "Build a prompt quality loop",
    description:
      "Create one prompt, test it, save a baseline, and compare the next version.",
    chips: ["prompt", "versioning"],
    steps: [
      {
        stage: "start_prompt",
        label: "Create prompt",
        description: "Start with one prompt the team can test.",
      },
      {
        stage: "run_prompt_test",
        label: "Run test",
        description: "Run one focused example before saving.",
      },
      {
        stage: "save_prompt_version",
        label: "Save version",
        description: "Create the baseline for future edits.",
      },
      {
        stage: "compare_prompt_versions",
        label: "Compare",
        description: "Review how the edited version changed behavior.",
      },
      {
        stage: "prompt_next_loop",
        label: "Improve",
        description: "Turn the result into a reusable example or check.",
      },
    ],
  },
  agent: {
    eyebrow: "Agent loop",
    title: "Prototype an agent with a quality check",
    description:
      "Create an agent, run one scenario, inspect the run, then save coverage.",
    chips: ["agent", "scenario"],
    steps: [
      {
        stage: "create_agent",
        label: "Create agent",
        description: "Start with one runnable agent.",
      },
      {
        stage: "run_agent_scenario",
        label: "Run scenario",
        description: "Exercise the agent on one task.",
      },
      {
        stage: "review_agent_trace",
        label: "Review run",
        description: "Inspect the trace and find the first signal.",
      },
      {
        stage: "save_agent_eval",
        label: "Save coverage",
        description: "Turn the reviewed run into repeatable coverage.",
      },
      {
        stage: "agent_create_eval",
        label: "Create eval",
        description: "Keep the agent behavior measurable.",
      },
    ],
  },
  gateway: {
    eyebrow: "Gateway loop",
    title: "Route one request safely",
    description:
      "Configure a provider, create a key, send one request, and turn the log into control.",
    chips: ["gateway", "traffic"],
    steps: [
      {
        stage: "configure_gateway_provider",
        label: "Add provider",
        description: "Connect the model provider to route traffic.",
      },
      {
        stage: "create_gateway_key",
        label: "Create key",
        description: "Create the key for the first request.",
      },
      {
        stage: "run_gateway_request",
        label: "Send request",
        description: "Send one request through the gateway.",
      },
      {
        stage: "review_gateway_log",
        label: "Review log",
        description: "Inspect status, latency, cost, and routing.",
      },
      {
        stage: "fix_gateway_failure",
        label: "Fix issue",
        description: "Resolve the first failure if one appears.",
      },
      {
        stage: "add_gateway_policy",
        label: "Add policy",
        description: "Create a guardrail for future requests.",
      },
    ],
  },
  evals: {
    eyebrow: "Eval loop",
    title: "Create one eval and review the first failure",
    description:
      "Add a small dataset, attach a scorer, run the eval, and inspect what failed.",
    chips: ["evals", "quality"],
    steps: [
      {
        stage: "create_eval_dataset",
        label: "Create dataset",
        description: "Start with a focused set of examples.",
      },
      {
        stage: "add_eval_scorer",
        label: "Add scorer",
        description: "Define the quality signal to measure.",
      },
      {
        stage: "run_eval",
        label: "Run eval",
        description: "Run the check once.",
      },
      {
        stage: "review_eval_failures",
        label: "Review failure",
        description: "Inspect the first useful failure.",
      },
      {
        stage: "eval_next_loop",
        label: "Improve",
        description: "Turn the failure into the next fix.",
      },
    ],
  },
  voice: {
    eyebrow: "Voice loop",
    title: "Connect a voice agent quality loop",
    description:
      "Create or connect a voice agent, run one call, review it, and add success criteria.",
    chips: ["voice", "call"],
    steps: [
      {
        stage: "create_voice_agent",
        label: "Create agent",
        description: "Start with one voice agent.",
      },
      {
        stage: "run_voice_test_call",
        label: "Run call",
        description: "Run a test call to collect a signal.",
      },
      {
        stage: "review_voice_call",
        label: "Review call",
        description: "Inspect the transcript and outcome.",
      },
      {
        stage: "add_voice_success_criteria",
        label: "Add criteria",
        description: "Define what a good call means.",
      },
      {
        stage: "voice_monitor_calls",
        label: "Monitor",
        description: "Keep watching live calls after setup.",
      },
    ],
  },
};

export const hasPathFocusPlan = (primaryPath) =>
  Boolean(PATH_FOCUS_PLANS[primaryPath]);
