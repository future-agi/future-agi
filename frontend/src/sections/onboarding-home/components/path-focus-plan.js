export const PATH_FOCUS_PLANS = {
  prompt: {
    eyebrow: "Prompt setup",
    title: "Test prompts and compare versions",
    description:
      "Create one prompt, test it, save a baseline, and compare the next version.",
    chips: ["prompt", "versioning"],
    steps: [
      {
        stage: "start_prompt",
        label: "Write the prompt you want to improve",
        description:
          "Bring in the prompt whose output you want to make better.",
        tourAnchor: "prompt_create_button",
      },
      {
        stage: "run_prompt_test",
        label: "See how it scores on real cases",
        description: "Run it on a real example and read the result.",
        tourAnchor: "prompt_run_test_button",
      },
      {
        stage: "save_prompt_version",
        label: "Lock a baseline to prove the next edit",
        description: "Save this version as the bar the next edit has to beat.",
        tourAnchor: "prompt_save_version_button",
      },
      {
        stage: "create_second_prompt_version",
        label: "Try an edit and rerun it",
        description: "Edit, rerun, and save a second version to compare.",
        tourAnchor: "prompt_create_second_version_button",
      },
      {
        stage: "compare_prompt_versions",
        label: "See which edit wins and which regresses",
        description: "Read the side-by-side to catch the silent regression.",
        tourAnchor: "prompt_compare_versions_button",
      },
      {
        stage: "prompt_next_loop",
        label: "Turn the win into a repeatable check",
        description: "Save the result as a reusable example or quality check.",
        tourAnchor: "prompt_add_example_button",
      },
    ],
  },
  agent: {
    eyebrow: "Agent setup",
    title: "Prototype an agent with eval coverage",
    description:
      "Create an agent, add one step, run a scenario, inspect the run, then save coverage.",
    chips: ["agent", "scenario"],
    steps: [
      {
        stage: "create_agent",
        label: "Stand up an agent you can run",
        description: "Start with one runnable agent to put through a scenario.",
        tourAnchor: "agent_create_button",
      },
      {
        stage: "add_agent_node",
        label: "Give it a prompt and a model",
        description: "Add a runnable prompt with a model and sample input.",
        tourAnchor: "agent_add_node_button",
      },
      {
        stage: "run_agent_scenario",
        label: "Watch your agent handle a real scenario",
        description: "Run it on one realistic task and watch it work.",
        tourAnchor: "agent_run_scenario_button",
      },
      {
        stage: "review_agent_trace",
        label: "See where it failed and why",
        description: "Open the trace to find the exact step that broke.",
        tourAnchor: "agent_trace_review_link",
      },
      {
        stage: "save_agent_eval",
        label: "Catch that failure automatically next time",
        description: "Turn the reviewed run into repeatable coverage.",
        tourAnchor: "agent_save_eval_button",
      },
      {
        stage: "agent_create_eval",
        label: "Keep the behavior measurable",
        description: "Add an eval so regressions surface on every run.",
        tourAnchor: "agent_create_eval_button",
      },
    ],
  },
  gateway: {
    eyebrow: "Gateway setup",
    title: "Route one request safely",
    description:
      "Configure a provider, create a key, send one request, and turn the log into control.",
    chips: ["gateway", "traffic"],
    steps: [
      {
        stage: "configure_gateway_provider",
        label: "Route your first request",
        description:
          "Connect a model provider so the gateway can route traffic.",
        tourAnchor: "gateway_provider_button",
      },
      {
        stage: "create_gateway_key",
        label: "Get a key to route through",
        description: "Create the key your first request will route through.",
        tourAnchor: "gateway_key_button",
      },
      {
        stage: "run_gateway_request",
        label: "See cost + latency per call",
        description:
          "Send one request and read its cost, latency, and routing.",
        tourAnchor: "gateway_request_button",
      },
      {
        stage: "review_gateway_log",
        label: "Trace where time and spend went",
        description: "Open the log to see status, latency, cost, and routing.",
        tourAnchor: "gateway_log_review_link",
      },
      {
        stage: "fix_gateway_failure",
        label: "Recover from the first failure",
        description: "Resolve the first failed request if one appears.",
        tourAnchor: "gateway_failure_button",
      },
      {
        stage: "add_gateway_policy",
        label: "Put guardrails on future traffic",
        description: "Add a policy that controls future requests.",
        tourAnchor: "gateway_policy_button",
      },
    ],
  },
  evals: {
    eyebrow: "Simulation / Evals",
    title: "Test AI and act on the first result",
    description:
      "Choose a small dataset, simulation, or trace source, add a quality check, run it, and fix what failed.",
    chips: ["evals", "quality"],
    steps: [
      {
        stage: "create_eval_dataset",
        label: "Pick what to test",
        description: "Point at focused examples, a simulation, or real traces.",
        tourAnchor: "eval_dataset_button",
      },
      {
        stage: "add_eval_scorer",
        label: "Define what good looks like",
        description: "Set the check that good output has to satisfy.",
        tourAnchor: "eval_scorer_button",
      },
      {
        stage: "run_eval",
        label: "See which examples pass and fail",
        description: "Run it once and open the pass/fail breakdown.",
        tourAnchor: "eval_run_button",
      },
      {
        stage: "review_eval_failures",
        label: "See exactly why each one failed",
        description:
          "Open the failures grouped by cause and pick the next fix.",
        tourAnchor: "eval_failure_review_link",
      },
      {
        stage: "eval_next_loop",
        label: "Fix the cause and rerun",
        description: "Fix the source or tune the check, then run it again.",
        tourAnchor: "eval_next_loop_button",
      },
    ],
  },
  voice: {
    eyebrow: "Voice setup",
    title: "Connect a voice AI agent",
    description:
      "Create or connect a voice AI agent, run one call, review it, and add success criteria.",
    chips: ["voice", "call"],
    steps: [
      {
        stage: "create_voice_agent",
        label: "Bring in a voice agent to test",
        description: "Create or connect one voice agent you can call.",
        tourAnchor: "voice_agent_button",
      },
      {
        stage: "run_voice_test_call",
        label: "Hear how a call goes",
        description:
          "Run a test call so there is a real conversation to review.",
        tourAnchor: "voice_test_call_button",
      },
      {
        stage: "review_voice_call",
        label: "See timing, interruptions, and outcome",
        description:
          "Open the call to inspect timing, interruptions, and result.",
        tourAnchor: "voice_call_review_link",
      },
      {
        stage: "add_voice_success_criteria",
        label: "Define what a good call sounds like",
        description: "Set the success criteria a good call has to meet.",
        tourAnchor: "voice_success_criteria_button",
      },
      {
        stage: "voice_monitor_calls",
        label: "Keep watching live calls",
        description: "Stay on top of real calls after setup.",
        tourAnchor: "voice_monitor_button",
      },
    ],
  },
};

export const hasPathFocusPlan = (primaryPath) =>
  Boolean(PATH_FOCUS_PLANS[primaryPath]);
