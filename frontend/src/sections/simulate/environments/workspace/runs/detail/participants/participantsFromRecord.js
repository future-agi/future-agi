/**
 * A call's worker record (GET /simulate/v3/call-executions/{id}/workers/) in the
 * participants shape `sequenceOf` draws: one entry per turn, each hand-off a
 * block with its tool calls, nested by the API's own `parent`. Only served
 * fields are read; a field the record lacks stays empty, never guessed.
 */
export const COORDINATOR_ID = "coordinator";

const seconds = (iso) => {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms / 1000;
};

// A record time (seconds since the record's start) on the recording's clock, which starts with the call.
export const recordingSeconds = (at, recordStartedAt, callStartedAt) => {
  if (at == null) return null;
  const gap =
    (seconds(recordStartedAt) ?? 0) -
    (seconds(callStartedAt) ?? seconds(recordStartedAt) ?? 0);
  return Math.max(0, at + gap);
};

const textOf = (shown, raw) => shown?.text ?? raw ?? null;

// A hand-off's end, as `sequenceOf` names a worker's state.
const STATE_OF_END = {
  finished: "done",
  passed: "done",
  failed: "failed",
  cut_off: "failed",
  no_return: "timeout",
};

export function participantsFromRecord(record) {
  if (!record) return null;
  const workerOf = Object.fromEntries(
    (record.workers || []).map((worker) => [worker.worker_id, worker]),
  );
  const stepOf = Object.fromEntries(
    (record.steps || []).map((step) => [step.key, step]),
  );
  const firstRequestAt = (record.turns || []).find((round) => round.request?.at)
    ?.request?.at;
  const zero = seconds(record.started_at) ?? seconds(firstRequestAt);
  const sinceStart = (iso) => {
    const at = seconds(iso);
    return at == null || zero == null ? null : Math.max(0, at - zero);
  };
  const laneOf = (workerId) => workerOf[workerId]?.id ?? workerId;

  const toCall = (step) => {
    const call = step.call || {};
    return {
      id: step.key,
      key: step.key,
      name: call.tool,
      at: sinceStart(call.at),
      input: call.input ?? null,
      output: call.output ?? null,
      inputOmitted: !!call.input_omitted,
      outputOmitted: !!call.output_omitted,
      failed: call.status === "error",
      refused: !!call.refusal_code,
      reason: call.refusal_code ?? null,
      spanId: call.span_id ?? null,
      traceId: call.trace_id ?? null,
      unattributed: call.attribution === "unattributed",
    };
  };

  const toBlocks = (step, parentLane, out) => {
    const worker = workerOf[step.worker_id];
    const children = (step.children || [])
      .map((key) => stepOf[key])
      .filter(Boolean);
    out.push({
      id: laneOf(step.worker_id),
      key: step.key,
      name: worker?.name ?? step.worker_id,
      undeclared: !!worker?.undeclared,
      state: step.transfer ? "transferred" : STATE_OF_END[step.end] ?? "done",
      transfer: !!step.transfer,
      unknownParent: step.relationship === "unknown",
      parentId: parentLane,
      delegatedAt: sinceStart(step.started_at ?? step.handoff?.at),
      endedAt: sinceStart(step.ended_at ?? step.returned?.at),
      task: textOf(step.handoff?.task_shown, step.handoff?.task),
      result: textOf(step.returned?.result_shown, step.returned?.result),
      recorded: true,
      endState: step.end_state ?? null,
      spanId: step.handoff?.span_id ?? null,
      traceId: step.handoff?.trace_id ?? null,
      calls: children.filter((child) => child.kind === "tool_call").map(toCall),
    });
    children
      .filter((child) => child.kind === "handoff")
      .forEach((child) => toBlocks(child, laneOf(step.worker_id), out));
  };

  const turns = (record.turns || []).map((round, index) => {
    const top = (round.children || [])
      .map((key) => stepOf[key])
      .filter(Boolean);
    const workers = [];
    top
      .filter((step) => step.kind === "handoff")
      .forEach((step) => toBlocks(step, null, workers));
    const calls = top.filter((step) => step.kind === "tool_call").map(toCall);
    return {
      n: round.turn ?? index + 1,
      at: sinceStart(round.request?.at),
      request: textOf(round.request?.shown, round.request?.text),
      reply: textOf(round.response?.shown, round.response?.text),
      replyAt: sinceStart(round.response?.at),
      workers,
      own: calls.filter((call) => !call.unattributed),
      untied: calls.filter((call) => call.unattributed),
    };
  });

  const unseen = (record.workers || []).filter((worker) => !worker.observed);
  return {
    observed: true,
    coordinator: {
      id: COORDINATOR_ID,
      name: record.coordinator?.name ?? null,
    },
    workers: [],
    undeclared: [],
    notRun: unseen.map((worker) => worker.name),
    notObserved: [],
    turns,
  };
}
