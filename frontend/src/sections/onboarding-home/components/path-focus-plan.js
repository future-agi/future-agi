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
        tourAnchor: "prompt_create_button",
      },
      {
        stage: "run_prompt_test",
        label: "Run test",
        description: "Run one focused example before saving.",
        tourAnchor: "prompt_run_test_button",
      },
      {
        stage: "save_prompt_version",
        label: "Save version",
        description: "Create the baseline for future edits.",
        tourAnchor: "prompt_save_version_button",
      },
      {
        stage: "create_second_prompt_version",
        label: "Second version",
        description: "Edit, run, and save one more version before comparing.",
        tourAnchor: "prompt_compare_versions_button",
      },
      {
        stage: "compare_prompt_versions",
        label: "Compare",
        description: "Review how the edited version changed behavior.",
        tourAnchor: "prompt_compare_versions_button",
      },
      {
        stage: "prompt_next_loop",
        label: "Improve",
        description: "Turn the result into a reusable example or check.",
        tourAnchor: "prompt_add_example_button",
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
        tourAnchor: "agent_create_button",
      },
      {
        stage: "run_agent_scenario",
        label: "Run scenario",
        description: "Exercise the agent on one task.",
        tourAnchor: "agent_run_scenario_button",
      },
      {
        stage: "review_agent_trace",
        label: "Review run",
        description: "Inspect the trace and find the first signal.",
        tourAnchor: "agent_trace_review_link",
      },
      {
        stage: "save_agent_eval",
        label: "Save coverage",
        description: "Turn the reviewed run into repeatable coverage.",
        tourAnchor: "agent_save_eval_button",
      },
      {
        stage: "agent_create_eval",
        label: "Create eval",
        description: "Keep the agent behavior measurable.",
        tourAnchor: "agent_create_eval_button",
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
        tourAnchor: "gateway_provider_button",
      },
      {
        stage: "create_gateway_key",
        label: "Create key",
        description: "Create the key for the first request.",
        tourAnchor: "gateway_key_button",
      },
      {
        stage: "run_gateway_request",
        label: "Send request",
        description: "Send one request through the gateway.",
        tourAnchor: "gateway_request_button",
      },
      {
        stage: "review_gateway_log",
        label: "Review log",
        description: "Inspect status, latency, cost, and routing.",
        tourAnchor: "gateway_log_review_link",
      },
      {
        stage: "fix_gateway_failure",
        label: "Fix issue",
        description: "Resolve the first failure if one appears.",
        tourAnchor: "gateway_failure_button",
      },
      {
        stage: "add_gateway_policy",
        label: "Add policy",
        description: "Create a guardrail for future requests.",
        tourAnchor: "gateway_policy_button",
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
        tourAnchor: "eval_dataset_button",
      },
      {
        stage: "add_eval_scorer",
        label: "Add scorer",
        description: "Define the quality signal to measure.",
        tourAnchor: "eval_scorer_button",
      },
      {
        stage: "run_eval",
        label: "Run eval",
        description: "Run the check once.",
        tourAnchor: "eval_run_button",
      },
      {
        stage: "review_eval_failures",
        label: "Review failure",
        description: "Inspect the first useful failure.",
        tourAnchor: "eval_failure_review_link",
      },
      {
        stage: "eval_next_loop",
        label: "Improve",
        description: "Turn the failure into the next fix.",
        tourAnchor: "eval_next_loop_button",
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
        tourAnchor: "voice_agent_button",
      },
      {
        stage: "run_voice_test_call",
        label: "Run call",
        description: "Run a test call to collect a signal.",
        tourAnchor: "voice_test_call_button",
      },
      {
        stage: "review_voice_call",
        label: "Review call",
        description: "Inspect the transcript and outcome.",
        tourAnchor: "voice_call_review_link",
      },
      {
        stage: "add_voice_success_criteria",
        label: "Add criteria",
        description: "Define what a good call means.",
        tourAnchor: "voice_success_criteria_button",
      },
      {
        stage: "voice_monitor_calls",
        label: "Monitor",
        description: "Keep watching live calls after setup.",
        tourAnchor: "voice_monitor_button",
      },
    ],
  },
};

export const hasPathFocusPlan = (primaryPath) =>
  Boolean(PATH_FOCUS_PLANS[primaryPath]);
