import { CALL_WORKERS_COPY as C } from "../callTraces.constants";

// About six lines of text; a longer answer opens on request.
const CLAMP_LINES = 6;
const CLAMP_CHARS = 480;

/** Epoch seconds of an API timestamp; null when it is not one. */
export const seconds = (iso) => {
  const value = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(value) ? null : value / 1000;
};

/** Whether an answer is long enough to fold. */
export const isLong = (text) =>
  text.split("\n").length > CLAMP_LINES || text.length > CLAMP_CHARS;

/** A text the API marked as data or prose, as the markdown that shows it; the fence outruns any backticks inside. */
export const asMarkdown = (shown) => {
  if (shown.format !== "code") return shown.text;
  const longest = Math.max(
    0,
    ...(shown.text.match(/`+/g) ?? []).map((run) => run.length),
  );
  const fence = "`".repeat(Math.max(3, longest + 1));
  return `${fence}\n${shown.text}\n${fence}`;
};

/** The API's steps by key, so a hand-off's or a turn's `children` name their rows. */
export const stepsByKey = (data) =>
  Object.fromEntries(data.steps.map((step) => [step.key, step]));

const toolEvent = (step, depth, names) => ({
  key: `call-${step.key}`,
  unknown: step.relationship === "unknown",
  traceId: step.call.trace_id,
  spanId: step.call.span_id,
  at: step.call.at,
  actor:
    step.call.attribution === "unattributed"
      ? C.unknown
      : names.ofWorker(step.call.worker_id),
  type: C.toolCallStep,
  depth,
  call: step.call,
});

const handOffEvent = (step, names) => ({
  key: `handoff-${step.key}`,
  at: step.handoff.at,
  actor: step.handoff.by ? names.ofWorker(step.handoff.by) : names.coordinator,
  type: step.transfer ? C.controlPassStep : C.handOffStep,
  unknown: step.relationship === "unknown",
  traceId: step.handoff.trace_id,
  spanId: step.handoff.span_id,
  depth: step.depth - 1,
  to: names.ofStep(step),
  parallel: step.parallel,
  shown: step.handoff.task_shown,
});

// How a hand-off ended, as a step. A control pass never returns, so it has none.
const endEvents = (step, names) => {
  if (step.transfer) return [];
  const base = {
    key: `end-${step.key}`,
    actor: names.ofStep(step),
    depth: step.depth,
  };
  if (step.returned)
    return [
      {
        ...base,
        at: step.returned.at,
        type: C.answerStep,
        bad: step.did_not_finish,
        shown: step.returned.result_shown,
        empty: C.noResult,
      },
    ];
  return [
    step.end === "cut_off"
      ? { ...base, type: C.notShownStep, note: C.returnCutOff(base.actor) }
      : {
          ...base,
          type: C.noReturnStep,
          bad: step.did_not_finish,
          note: C.noReturn(base.actor),
        },
  ];
};

const stepsOf = (step, byKey, names, nested) => [
  ...(step.handoff ? [handOffEvent(step, names)] : []),
  ...step.children.flatMap((key) => {
    const child = byKey[key];
    if (child.kind === "tool_call")
      return [toolEvent(child, step.depth, names)];
    return nested ? stepsOf(child, byKey, names, true) : [];
  }),
  ...endEvents(step, names),
];

/** One hand-off's own steps, leaving out the hand-offs it made, which have their own table rows. */
export const handOffEvents = (step, byKey, names) =>
  stepsOf(step, byKey, names, false);

const stepsOfKeys = (keys, byKey, names) =>
  keys.flatMap((key) => {
    const step = byKey[key];
    return step.kind === "tool_call"
      ? [toolEvent(step, 0, names)]
      : stepsOf(step, byKey, names, true);
  });

/** How a turn is named beside its request and reply: only when the call has more than one. */
export const turnLabel = (round, rounds) =>
  rounds.length > 1 ? `${C.turn(round.turn)} · ` : "";

/** Every step of a call in reading order: each turn's request, its steps, then its reply. */
export const callEvents = (data, byKey, names) => {
  if (!data.counts.hand_offs)
    return stepsOfKeys(
      data.steps.filter((step) => step.parent === null).map((step) => step.key),
      byKey,
      names,
    );
  return data.turns.flatMap((round) => [
    // Work before the caller spoke, or in no recorded turn, has no request to show.
    ...(!round.expects_messages && !round.request
      ? []
      : [
          {
            key: `request-${round.turn}`,
            at: round.request?.at,
            actor: C.caller,
            type: `${turnLabel(round, data.turns)}${C.messageStep}`,
            depth: 0,
            shown: round.request?.shown,
            empty: C.noRequest,
          },
        ]),
    ...stepsOfKeys(round.children, byKey, names),
    ...(!round.expects_messages && !round.response
      ? []
      : [
          {
            key: `reply-${round.turn}`,
            at: round.response?.at,
            actor: names.coordinator,
            type: `${turnLabel(round, data.turns)}${C.replyStep}`,
            depth: 0,
            shown: round.response?.shown,
            empty: C.noReply,
          },
        ]),
  ]);
};
