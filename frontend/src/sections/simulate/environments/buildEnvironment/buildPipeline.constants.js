// The full build pipeline as data, grouped by milestone. The header's coarse
// milestones (Agent, Contract, Environment, Scenarios) sit on top of twelve
// steps: seven in the "setup" phase (building the environment) and five in the
// "run" phase (the first run). Each step's live status is derived from the
// `done` milestone set the builder publishes, so the popover and the header
// cannot drift. Ported verbatim from the designer's _mock/buildPipeline.js.

import {
  stages,
  stageState,
  STAGE_STATE,
  terminalStages,
  readable,
} from "src/pages/dashboard/harness/harnessShared";

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

// The backend stages that make up each build milestone — the same grouping the
// product uses for its tabs (harnessShared TAB_STAGES / EVENT_STAGE_GROUPS).
const MILESTONE_STAGES = {
  understand: ["understanding_agent"],
  build: ["generating_environment", "building_environment", "generating_data"],
  scenarios: ["generating_scenarios", "validating_environment", "validating_scenarios"],
};

// Map a real harness job (getHarnessJob) into the `{ done, running, failure }`
// slice the header + building pane read — the live replacement for the mock
// timers. `done` stays milestone-keyed (understand|build|scenarios) so
// STAGE_ORDER.every and the deriving labels keep working. Reuses the product's
// `stageState` verbatim: an early/unknown stage (queued/admitted) credits
// nothing, and a failure anchors on `status.failure.stage` exactly as the
// product stepper does.
export const jobToBuildProgress = (job) => {
  const status = job?.status;
  if (!status) return { done: [], running: false, failure: null };
  const events = job.events || [];
  const stage = status.stage;

  const done = [];
  Object.entries(MILESTONE_STAGES).forEach(([milestone, group]) => {
    const groupStates = group.map((name) =>
      stageState(status, stages.indexOf(name), events),
    );
    const complete =
      stage === "completed" ||
      (groupStates.length && groupStates.every((s) => s === STAGE_STATE.DONE));
    if (complete) done.push(milestone);
  });

  const running = !terminalStages.has(stage);

  let failure = null;
  if (stage === "canceled") {
    // A cancel is terminal but not a step failure. Mark it so the view freezes
    // the deriving animation (it keys off `failure`), with a null stepId so the
    // pipeline stops where it was rather than lighting a step red.
    failure = {
      stepId: null,
      title: "canceled",
      detail: "You stopped this build.",
      retryable: false,
      canceled: true,
    };
  } else if (stage === "failed") {
    const failStage = status.failure?.stage;
    const milestone =
      Object.keys(MILESTONE_STAGES).find((m) =>
        MILESTONE_STAGES[m].includes(failStage),
      ) || BUILD_PIPELINE.find((s) => !done.includes(s.milestone))?.milestone;
    const step = BUILD_PIPELINE.find((s) => s.milestone === milestone);
    failure = {
      stepId: step?.id || BUILD_PIPELINE[0].id,
      title: readable(failStage || "build"),
      detail: status.failure?.message || readable(failStage || ""),
      retryable: status.failure?.domain === "infrastructure",
    };
  }

  return { done, running, failure };
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
