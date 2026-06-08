const fallbackStepStatus = ({ index, activeIndex }) => {
  if (index < activeIndex) return "complete";
  if (index === activeIndex) return "current";
  return "queued";
};

const OBSERVE_FALLBACK_STEPS = [
  {
    id: "connect_observability",
    stage: "connect_observability",
    label: "Open package setup",
    description:
      "Choose the SDK package, create the project, and run one request.",
    tourAnchor: "observe_create_project_button",
  },
  {
    id: "send_first_trace",
    stage: "waiting_for_first_trace",
    label: "Send first trace",
    description: "Send one trace from the app, SDK, or a test request.",
    tourAnchor: "observe_send_trace_button",
  },
  {
    id: "review_first_trace",
    stage: "review_first_trace",
    label: "Review first trace",
    description: "Inspect latency, cost, input, output, and errors.",
    tourAnchor: "observe_trace_review_link",
  },
  {
    id: "create_trace_evaluator",
    stage: "create_trace_evaluator",
    label: "Create quality check",
    description: "Turn the reviewed trace into a repeatable check.",
    tourAnchor: "observe_evaluator_button",
  },
];

const OBSERVE_FALLBACK_STAGE_INDEX = {
  activated: 3,
  connect_observability: 0,
  create_trace_evaluator: 3,
  daily_review: 3,
  review_first_trace: 2,
  waiting_for_first_trace: 1,
  waiting_for_first_trace_sample_available: 1,
};

export const observeFallbackJourneyPlan = (stage) => {
  const currentStepIndex = OBSERVE_FALLBACK_STAGE_INDEX[stage] ?? 0;
  return {
    id: "observe_first_setup",
    primaryPath: "observe",
    eyebrow: "Connect your agent",
    title: "Connect your agent",
    description:
      "Create the project, send one trace, review the trace, then add a quality check.",
    chips: ["setup"],
    currentStepIndex,
    steps: OBSERVE_FALLBACK_STEPS.map((step, index) => ({
      ...step,
      status: fallbackStepStatus({ index, activeIndex: currentStepIndex }),
    })),
  };
};
