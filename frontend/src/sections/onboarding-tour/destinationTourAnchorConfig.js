import { PATH_FOCUS_PLANS } from "src/sections/onboarding-home/components/path-focus-plan";

export const DESTINATION_TOUR_ANCHORS = [
  "observe_create_project_button",
  "observe_send_trace_button",
  "observe_trace_review_link",
  "observe_evaluator_button",
  "sample_project_button",
  "sample_trace_link",
  "sample_connect_real_data_button",
  "prompt_create_button",
  "prompt_run_test_button",
  "prompt_save_version_button",
  "prompt_create_second_version_button",
  "prompt_compare_versions_button",
  "prompt_add_example_button",
  "agent_create_button",
  "agent_add_node_button",
  "agent_run_scenario_button",
  "agent_trace_review_link",
  "agent_save_eval_button",
  "agent_create_eval_button",
  "gateway_provider_button",
  "gateway_key_button",
  "gateway_request_button",
  "gateway_log_review_link",
  "gateway_failure_button",
  "gateway_policy_button",
  "eval_dataset_button",
  "eval_scorer_button",
  "eval_run_button",
  "eval_failure_review_link",
  "eval_next_loop_button",
  "voice_agent_button",
  "voice_test_call_button",
  "voice_call_review_link",
  "voice_success_criteria_button",
  "voice_monitor_button",
];

export const DESTINATION_TOUR_STEP_COPY = {
  add_eval_scorer: {
    label: "Add quality check",
    description:
      "Use the highlighted action to define what good output should satisfy.",
  },
  add_gateway_policy: {
    label: "Add policy",
    description: "Use the highlighted action to add the first gateway control.",
  },
  add_voice_success_criteria: {
    label: "Add criteria",
    description: "Use the highlighted action to define what a good call means.",
  },
  agent_create_eval: {
    label: "Create quality check",
    description:
      "Use the highlighted action to make this agent behavior measurable.",
  },
  compare_prompt_versions: {
    label: "Compare versions",
    description:
      "Use the highlighted action to compare this prompt against a baseline.",
  },
  configure_gateway_provider: {
    label: "Add provider",
    description:
      "Use the highlighted action to connect the first model provider.",
  },
  connect_observability: {
    label: "Connect observability",
    description:
      "Use the highlighted action to create or review the observe setup.",
  },
  connect_real_data: {
    label: "Connect real data",
    description: "Use the highlighted action to move from sample to setup.",
  },
  create_agent: {
    label: "Create agent",
    description: "Use the highlighted action to start the agent loop.",
  },
  add_agent_node: {
    label: "Add starter prompt",
    description: "Use the highlighted action to add a runnable starter prompt.",
  },
  create_eval_dataset: {
    label: "Choose source",
    description:
      "Use the highlighted action to choose examples, a simulation, or traces.",
  },
  create_gateway_key: {
    label: "Create key",
    description: "Use the highlighted action to create the first gateway key.",
  },
  create_prompt: {
    label: "Create prompt",
    description: "Use the highlighted action to start the prompt loop.",
  },
  create_second_prompt_version: {
    label: "Create second version",
    description:
      "Use the highlighted action to create, run, and save one more version.",
  },
  create_trace_evaluator: {
    label: "Create quality check",
    description:
      "Use the highlighted action to turn this trace into a repeatable quality check.",
  },
  create_voice_agent: {
    label: "Create agent",
    description: "Use the highlighted action to start the voice workflow.",
  },
  eval_next_loop: {
    label: "Fix or finish",
    description:
      "Use the highlighted action to fix the source or tune the quality check.",
  },
  fix_gateway_failure: {
    label: "Fix issue",
    description:
      "Use the highlighted action to resolve the first gateway failure.",
  },
  open_sample_project: {
    label: "Open sample",
    description: "Use the highlighted action to open a ready-made signal.",
  },
  prompt_next_loop: {
    label: "Capture example",
    description:
      "Use the highlighted action to save one concrete failure example.",
  },
  review_agent_trace: {
    label: "Review run",
    description:
      "Use the highlighted action to inspect the first agent signal.",
  },
  review_eval_failures: {
    label: "Review result",
    description:
      "Use the highlighted action to inspect the first result and choose the next action.",
  },
  review_first_trace: {
    label: "Review signal",
    description:
      "Use the highlighted action to inspect the first trace signal.",
  },
  review_gateway_log: {
    label: "Review log",
    description:
      "Use the highlighted action to inspect status, latency, cost, and routing.",
  },
  review_sample_signal: {
    label: "Review issue",
    description: "Use the highlighted action to inspect the sample signal.",
  },
  review_voice_call: {
    label: "Review call",
    description:
      "Use the highlighted action to inspect the transcript and outcome.",
  },
  run_agent_scenario: {
    label: "Run scenario",
    description: "Save the draft if prompted, then run one scenario.",
  },
  run_eval: {
    label: "Run quality check",
    description: "Use the highlighted action to run the first quality check.",
  },
  run_gateway_request: {
    label: "Send request",
    description:
      "Use the highlighted action to send one request through the gateway.",
  },
  run_prompt_test: {
    label: "Run test",
    description:
      "Use the highlighted action to generate the first prompt result.",
  },
  run_voice_test_call: {
    label: "Run call",
    description:
      "Use the highlighted action to collect the first voice signal.",
  },
  save_agent_eval: {
    label: "Save coverage",
    description:
      "Use the highlighted action to turn this run into repeatable coverage.",
  },
  save_prompt_version: {
    label: "Save version",
    description:
      "Use the highlighted action to save the tested prompt baseline.",
  },
  send_first_trace: {
    label: "Send trace",
    description:
      "Use the highlighted action to send or inspect the first trace.",
  },
  start_prompt: {
    label: "Create prompt",
    description: "Use the highlighted action to start the prompt loop.",
  },
  voice_monitor_calls: {
    label: "Monitor calls",
    description:
      "Use the highlighted action to keep watching live calls after setup.",
  },
};

export const DEFAULT_DESTINATION_TOUR_COPY = {
  label: "Next step",
  description: "Use the highlighted action to continue setup.",
};

export const destinationTourCopyForStep = (journeyStep) =>
  DESTINATION_TOUR_STEP_COPY[journeyStep] || DEFAULT_DESTINATION_TOUR_COPY;

const DESTINATION_TOUR_PROGRESS_PLANS = {
  observe: {
    title: "Observe loop",
    steps: [
      {
        stage: "connect_observability",
        label: "Connect observability",
        tourAnchor: "observe_create_project_button",
      },
      {
        stage: "send_first_trace",
        label: "Send trace",
        tourAnchor: "observe_send_trace_button",
      },
      {
        stage: "review_first_trace",
        label: "Review signal",
        tourAnchor: "observe_trace_review_link",
      },
      {
        stage: "create_trace_evaluator",
        label: "Create quality check",
        tourAnchor: "observe_evaluator_button",
      },
    ],
  },
  sample: {
    title: "Sample loop",
    steps: [
      {
        stage: "open_sample_project",
        label: "Open sample",
        tourAnchor: "sample_project_button",
      },
      {
        stage: "review_sample_signal",
        label: "Review issue",
        tourAnchor: "sample_trace_link",
      },
      {
        stage: "connect_real_data",
        label: "Connect real data",
        tourAnchor: "sample_connect_real_data_button",
      },
    ],
  },
  ...PATH_FOCUS_PLANS,
};

export const destinationTourProgressForStep = ({
  journeyStep,
  tourAnchor,
} = {}) => {
  const plan = Object.values(DESTINATION_TOUR_PROGRESS_PLANS).find(
    (candidate) =>
      candidate.steps?.some(
        (step) => step.stage === journeyStep || step.tourAnchor === tourAnchor,
      ),
  );
  if (!plan) return null;

  const currentIndex = plan.steps.findIndex(
    (step) => step.stage === journeyStep || step.tourAnchor === tourAnchor,
  );
  if (currentIndex < 0) return null;

  return {
    currentLabel: plan.steps[currentIndex].label,
    nextLabel: plan.steps[currentIndex + 1]?.label || null,
    planTitle: plan.title,
    stepCount: plan.steps.length,
    stepNumber: currentIndex + 1,
  };
};
