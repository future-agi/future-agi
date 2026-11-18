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

// The moment record times count from: its own start, else the first request.
export const recordStart = (record) =>
  record?.started_at ??
  (record?.turns || []).find((round) => round.request?.at)?.request?.at ??
  null;

// A record time (seconds since recordStart) on the recording's clock, which starts with the call.
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
  no_return: "timeout",
  // The record was cut short: it can't say whether an answer came.
  cut_off: "cut_off",
};

export function participantsFromRecord(record) {
  if (!record) return null;
  const workerOf = Object.fromEntries(
    (record.workers || []).map((worker) => [worker.worker_id, worker]),
  );
  const stepOf = Object.fromEntries(
    (record.steps || []).map((step) => [step.key, step]),
  );
  const zero = seconds(recordStart(record));
  const sinceStart = (iso) => {
    const at = seconds(iso);
    return at == null || zero == null ? null : Math.max(0, at - zero);
  };
  const laneOf = (workerId) => workerOf[workerId]?.id ?? workerId;

  const toCall = (step) => {
    const call = step.call || {};
    // The API times a tool call on the call itself.
    const start = seconds(call.at);
    const end = seconds(call.ended_at);
    const by = step.worker_id ? workerOf[step.worker_id] : null;
    return {
      id: step.key,
      key: step.key,
      name: call.tool,
      at: sinceStart(call.at),
      durationMs:
        start != null && end != null && end >= start
          ? Math.round((end - start) * 1000)
          : null,
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
      // A worker's call outside any hand-off the record ties it to: its lane, under no hand-off.
      by: step.worker_id ? laneOf(step.worker_id) : null,
      byName: by?.name ?? step.worker_id ?? null,
      byUndeclared: !!by?.undeclared,
      looseParent: step.relationship === "unknown",
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
      state: step.transfer
        ? "transferred"
        : STATE_OF_END[step.end] ?? "unknown",
      transfer: !!step.transfer,
      unknownParent: step.relationship === "unknown",
      parentId: parentLane,
      delegatedAt: sinceStart(step.started_at ?? step.handoff?.at),
      endedAt: sinceStart(step.ended_at ?? step.returned?.at),
      task: textOf(step.handoff?.task_shown, step.handoff?.task),
      taskShown: step.handoff?.task_shown ?? null,
      result: textOf(step.returned?.result_shown, step.returned?.result),
      resultShown: step.returned?.result_shown ?? null,
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

  const turns = (record.turns || []).map((round) => {
    const top = (round.children || [])
      .map((key) => stepOf[key])
      .filter(Boolean);
    const workers = [];
    top
      .filter((step) => step.kind === "handoff")
      .forEach((step) => toBlocks(step, null, workers));
    const calls = top.filter((step) => step.kind === "tool_call").map(toCall);
    return {
      // Null when the record does not say which turn: never a number of ours.
      n: round.turn,
      at: sinceStart(round.request?.at),
      request: textOf(round.request?.shown, round.request?.text),
      requestShown: round.request?.shown ?? null,
      reply: textOf(round.response?.shown, round.response?.text),
      replyShown: round.response?.shown ?? null,
      replyAt: sinceStart(round.response?.at),
      workers,
      own: calls.filter((call) => !call.unattributed),
      untied: calls.filter((call) => call.unattributed),
    };
  });

  return {
    coordinator: {
      id: COORDINATOR_ID,
      name: record.coordinator?.name ?? null,
    },
    // Declared but no spans or tool calls of theirs arrived; they may still have run.
    notObserved: (record.workers || [])
      .filter((worker) => !worker.observed)
      .map((worker) => worker.name),
    turns,
  };
}
