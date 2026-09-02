/**
 * The full build pipeline.
 *
 * The header's four milestones — Agent, Contract, Environment, Scenarios — are
 * the coarse view. Underneath, the builder runs twelve steps against the
 * source, then the first run has four more. That detail used to live only in
 * the streaming chat and vanished with each new message, so "what happened
 * behind Environment being ticked green" had no answer once the chat scrolled.
 *
 * This is the same list rendered as data, grouped by milestone. The status
 * of each step is derived from the `done` set the builder already publishes —
 * one source of truth, so the popover and the header cannot drift.
 */

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

/**
 * Compute the pipeline's live status.
 *
 * `done` is the builder's own set of milestone ids, so the mapping is direct:
 * a milestone being complete implies every step under it is complete. The
 * first pending step becomes the running one only while `running` is true;
 * otherwise it stays "pending" so a paused pipeline does not look like a
 * failed one.
 */
/**
 * Live pipeline status.
 *
 * `failure` is optional: `{ stepId, title, detail, retryable }`. When present,
 * that step lands as "failed" and every step after it stays pending — a
 * failure halts the pipeline, so pretending the next step is "up next" would
 * be misleading. The row can be expanded in the popover to see what went
 * wrong and to retry.
 */
export const pipelineStatus = (done = [], running = false, currentPhase = "setup", failure = null) => {
  const doneSet = new Set(done);
  let seenPending = false;
  let halted = false;

  return BUILD_PIPELINE.map((step) => {
    if (failure && failure.stepId === step.id) {
      halted = true;
      return { ...step, status: "failed", failure };
    }
    /* Steps in the "run" phase only start once we actually kick off a run;
       until then they stay pending regardless of milestone completion. */
    const skip = step.phase === "run" && currentPhase !== "run";
    const complete = !skip && doneSet.has(step.milestone);
    let status = complete ? "done" : "pending";

    if (halted) return { ...step, status: "pending" };
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
  return {
    done,
    total: steps.length,
    running,
    failed,
    label: failed
      ? `Failed at ${failed.label.toLowerCase()}`
      : running
        ? `Running · ${done} of ${steps.length}`
        : `${done} of ${steps.length} complete`,
  };
};
