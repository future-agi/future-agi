// The full build pipeline as data, grouped by milestone. The header's coarse
// milestones (Agent, Contract, Environment, Scenarios) sit on top of twelve
// steps: seven in the "setup" phase (building the environment) and five in the
// "run" phase (the first run). Each step's live status is derived from the
// `done` milestone set the builder publishes, so the popover and the header
// cannot drift. Ported verbatim from the designer's _mock/buildPipeline.js.

export const PIPELINE_PHASE = { SETUP: "setup", RUN: "run" };

export const MILESTONE = {
  UNDERSTAND: "understand",
  BUILD: "build",
  SCENARIOS: "scenarios",
  AGENT: "agent",
};

export const STEP_STATUS = {
  DONE: "done",
  RUNNING: "running",
  PENDING: "pending",
  FAILED: "failed",
};

export const BUILD_PIPELINE = [
  {
    id: "understand",
    phase: "setup",
    milestone: "understand",
    label: "Understanding agent",
    detail: "Reading tools, prompts, guardrails and data from the source",
  },
  {
    id: "generate-env",
    phase: "setup",
    milestone: "build",
    label: "Generating environment",
    detail: "Deriving the world the tools act on",
  },
  {
    id: "build-env",
    phase: "setup",
    milestone: "build",
    label: "Building environment",
    detail: "Writing handlers so every tool call hits real state",
  },
  {
    id: "validate-env",
    phase: "setup",
    milestone: "build",
    label: "Validating environment",
    detail: "Every tool answered truthfully, including a truthful refusal",
  },
  {
    id: "generate-data",
    phase: "setup",
    milestone: "build",
    label: "Generating data",
    detail: "Seeding the awkward rows the use cases actually need",
  },
  {
    id: "generate-scenarios",
    phase: "setup",
    milestone: "scenarios",
    label: "Generating scenarios",
    detail: "Drafting one scenario per real use case, with sub-goals",
  },
  {
    id: "validate-scenarios",
    phase: "setup",
    milestone: "scenarios",
    label: "Validating scenarios",
    detail: "Three gates: ready, solvable, not vacuous — kept only if all three pass",
  },
  {
    id: "connect-agent",
    phase: "run",
    milestone: "agent",
    label: "Connecting agent",
    detail: "Wiring the shadow agent to the sandbox for the first run",
  },
  {
    id: "run",
    phase: "run",
    milestone: "agent",
    label: "Running",
    detail: "Playing the scenarios against the agent, one episode at a time",
  },
  {
    id: "grade",
    phase: "run",
    milestone: "agent",
    label: "Grading",
    detail: "Settling every check from world state and tool logs",
  },
  {
    id: "upload",
    phase: "run",
    milestone: "agent",
    label: "Uploading artifacts",
    detail: "Persisting traces, transcripts, tool logs and eval results",
  },
  {
    id: "completed",
    phase: "run",
    milestone: "agent",
    label: "Completed",
    detail: "The first run is on record and the environment is ready to iterate",
  },
];

export const STAGE_ORDER = ["understand", "build", "scenarios"];

export const PIPELINE_PHASES = [
  { id: "setup", label: "Setup — building the environment" },
  { id: "run", label: "First run — putting the agent through it" },
];

// A gentle, deterministic "duration" per step, so completed rows have a real
// number to show on the loading screen. Not persisted anywhere.
export const STEP_DURATION = {
  understand: 2.4,
  "generate-env": 1.6,
  "build-env": 3.2,
  "validate-env": 1.8,
  "generate-data": 2.9,
  "generate-scenarios": 3.6,
  "validate-scenarios": 2.1,
};

// Compute the pipeline's live status. `done` is the builder's own set of
// milestone ids, so the mapping is direct: a milestone being complete implies
// every step under it is complete. The first pending step becomes the running
// one only while `running` is true; otherwise it stays pending so a paused
// pipeline does not look like a failed one. `failure` is optional
// ({ stepId, title, detail, retryable }); when present that step lands "failed"
// and every step after it stays pending — a failure halts the pipeline.
export const pipelineStatus = (done = [], running = false, currentPhase = "setup", failure = null) => {
  const doneSet = new Set(done);
  let seenPending = false;
  let halted = false;

  return BUILD_PIPELINE.map((step) => {
    if (failure && failure.stepId === step.id) {
      halted = true;
      return { ...step, status: "failed", failure };
    }
    // Steps in the "run" phase only start once we actually kick off a run;
    // until then they stay pending regardless of milestone completion.
    const skip = step.phase === "run" && currentPhase !== "run";
    const complete = !skip && doneSet.has(step.milestone);
    let status = complete ? "done" : "pending";

    if (halted) return { ...step, status: "pending" };
    // A run-phase step never shows as "running" while we're still in setup, so a
    // chat message or aside chip after 7/7 can't relight the pipeline / pill.
    if (skip) return { ...step, status: "pending" };
    if (!complete && !seenPending) {
      status = running ? "running" : "pending";
      seenPending = true;
    }
    return { ...step, status };
  });
};

export const pipelineSummary = (steps) => {
  const done = steps.filter((s) => s.status === "done").length;
  const running = steps.some((s) => s.status === "running");
  const failed = steps.find((s) => s.status === "failed") || null;

  let label;
  if (failed) {
    label = `Failed at ${failed.label.toLowerCase()}`;
  } else if (running) {
    label = `Running · ${done} of ${steps.length}`;
  } else {
    label = `${done} of ${steps.length} complete`;
  }

  return { done, total: steps.length, running, failed, label };
};
