import { CALL_WORKERS_COPY as C } from "../callTraces.constants";

// About six lines of text; a longer answer opens on request.
const CLAMP_LINES = 6;
const CLAMP_CHARS = 480;
const HAS_ZONE = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/i;
// A printed dict opens on a key; a printed list on a quoted, nested or literal item. `…` ends a cut-off one.
const PRINTED_DICT = /^\{\s*(?:(['"]).*?\1|\w+)\s*:/;
const PRINTED_LIST =
  /^\[\s*(?:['"{[]|(?:-?\d[\d.]*|True|False|None)\s*(?:,|\]$)|\]$)/;

/** Epoch seconds of a stored timestamp, read as UTC when it carries no zone; null when it is not one. */
export const seconds = (iso) => {
  const value = iso ? Date.parse(HAS_ZONE.test(iso) ? iso : `${iso}Z`) : NaN;
  return Number.isNaN(value) ? null : value / 1000;
};

/** Whether an answer is long enough to fold. */
export const isLong = (text) =>
  text.split("\n").length > CLAMP_LINES || text.length > CLAMP_CHARS;

const looksPrinted = (body) =>
  /[}\]…]$/.test(body) && (PRINTED_DICT.test(body) || PRINTED_LIST.test(body));

/** An answer returned as data (JSON, or a printed dict or list) as a code block; other text as it came. */
export const asShown = (text) => {
  const body = text.trim();
  if (!/^[[{]/.test(body)) return text;
  let code;
  try {
    code = JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    // Prose that opens with a bracket stays text; a printed or cut-off object stays as it came.
    if (!looksPrinted(body)) return text;
    code = body;
  }
  return `\`\`\`\n${code}\n\`\`\``;
};

/**
 * The API's steps (reading order, each naming the hand-off it sits under) as the nested rows the panel draws.
 * Grouping only: which hand-off owns what, which turn it happened in, spans, depth and parallel runs all come from
 * the API.
 */
export const nest = (data) => {
  const byKey = {};
  const items = [];
  const top = {};
  for (const step of data.steps || []) {
    const row =
      step.kind === "handoff"
        ? {
            kind: "handoff",
            key: step.key,
            worker_id: step.worker_id,
            depth: step.depth,
            transfer: step.transfer,
            delegated: step.handoff,
            returned: step.returned,
            parallel: step.parallel,
            relationship: step.relationship,
            items: [],
          }
        : {
            kind: "call",
            key: step.key,
            event: step.call,
            relationship: step.relationship,
          };
    if (row.kind === "handoff") byKey[step.key] = row;
    if (step.parent === null) top[step.key] = row;
    (step.parent === null ? items : byKey[step.parent]?.items || items).push(
      row,
    );
  }
  // One block per turn the API returned, each with the top-level rows that happened in it.
  const rounds = (data.turns || []).map((round) => ({ ...round, items: [] }));
  const roundOf = Object.fromEntries(
    rounds.map((round) => [round.turn, round]),
  );
  for (const step of data.steps || []) {
    if (step.parent === null) roundOf[step.turn].items.push(top[step.key]);
  }
  return {
    items,
    rounds,
    depth: data.depth ?? 0,
    parallel: data.parallel ?? 0,
    overlapUnknown: Boolean(data.overlap_unknown),
    depthOf: data.worker_depths || {},
  };
};

/** Every hand-off in a nested tree, each followed by the ones it made. */
export const handOffRows = (items) =>
  items.flatMap((item) =>
    item.kind === "handoff" ? [item, ...handOffRows(item.items)] : [],
  );

const hasFailedCall = (block) =>
  block.items.some(
    (item) => item.kind === "call" && item.event.status === "error",
  );

/**
 * How a hand-off ended. A return decides it, even after a tool failed, since the worker may have recovered.
 * A control pass never returns, so only a failed tool marks it; a record cut at its limit can't say.
 */
export const handOffEnd = (block, cutOff) => {
  if (block.returned)
    return block.returned.status === "error" ? "failed" : "finished";
  if (block.transfer) return hasFailedCall(block) ? "failed" : "passed";
  return cutOff ? "cut_off" : "no_return";
};

/** How many hand-offs ended each way, and how many workers they went to. */
export const handOffCounts = (rows, cutOff) => {
  const ends = rows.map((row) => handOffEnd(row, cutOff));
  const count = (...kinds) => ends.filter((end) => kinds.includes(end)).length;
  return {
    handOffs: rows.length,
    workers: new Set(rows.map((row) => row.worker_id)).size,
    finished: count("finished"),
    didNotFinish: count("failed", "no_return"),
    passed: count("passed"),
    cutOff: count("cut_off"),
  };
};

const toolEvent = (event, depth, names, relationship) => ({
  key: `call-${event.seq}`,
  unknown: relationship === "unknown",
  traceId: event.trace_id,
  at: event.at,
  actor:
    event.attribution === "unattributed"
      ? C.unknown
      : names.ofWorker(event.worker_id),
  type: C.toolCallStep,
  depth,
  call: event,
});

const handOffEvent = (block, names) => ({
  key: `handoff-${block.key}`,
  at: block.delegated.at,
  actor: block.delegated.by
    ? names.ofWorker(block.delegated.by)
    : names.coordinator,
  type: block.transfer ? C.controlPassStep : C.handOffStep,
  unknown: block.relationship === "unknown",
  traceId: block.delegated.trace_id,
  depth: block.depth - 1,
  to: names.ofBlock(block),
  parallel: block.parallel,
  text: block.delegated.task,
});

// How a hand-off ended, as a step. A control pass never returns, so it has none.
const endEvents = (block, names, cutOff) => {
  if (block.transfer) return [];
  const base = {
    key: `end-${block.key}`,
    actor: names.ofBlock(block),
    depth: block.depth,
  };
  if (block.returned)
    return [
      {
        ...base,
        at: block.returned.at,
        type: C.answerStep,
        bad: block.returned.status === "error",
        text: block.returned.result,
        empty: C.noResult,
      },
    ];
  return [
    cutOff
      ? {
          ...base,
          type: C.notShownStep,
          note: C.returnCutOff(base.actor),
        }
      : {
          ...base,
          type: C.noReturnStep,
          bad: true,
          note: C.noReturn(base.actor),
        },
  ];
};

const stepsOf = (block, names, cutOff, nested) => [
  ...(block.delegated ? [handOffEvent(block, names)] : []),
  ...block.items.flatMap((item) => {
    if (item.kind === "call")
      return [toolEvent(item.event, block.depth, names, item.relationship)];
    return nested ? stepsOf(item, names, cutOff, true) : [];
  }),
  ...endEvents(block, names, cutOff),
];

/** One hand-off's own steps, leaving out the hand-offs it made, which have their own table rows. */
export const handOffEvents = (block, names, cutOff) =>
  stepsOf(block, names, cutOff, false);

const stepsOfItems = (items, names, cutOff) =>
  items.flatMap((item) =>
    item.kind === "call"
      ? [toolEvent(item.event, 0, names, item.relationship)]
      : stepsOf(item, names, cutOff, true),
  );

/** How a turn is named beside its request and reply: only when the call has more than one. */
export const turnLabel = (round, rounds) =>
  rounds.length > 1 ? `${C.turn(round.turn)} · ` : "";

/**
 * Whether a turn's missing request or reply is left out rather than shown as not seen: work before the caller spoke,
 * and steps whose turn the record does not say, have no message of their own.
 */
export const noMessages = (round, rounds) =>
  round.turn === null || (round.turn === 0 && rounds.length > 1);

/** Every step of a call in reading order: each turn's request, its steps, then its reply. */
export const callEvents = (data, tree, names, cutOff, hasHandOffs) => {
  if (!hasHandOffs) return stepsOfItems(tree.items, names, cutOff);
  return tree.rounds.flatMap((round) => [
    // Work before the caller spoke, or in no recorded turn, has no request to show.
    ...(noMessages(round, tree.rounds) && !round.request
      ? []
      : [
          {
            key: `request-${round.turn}`,
            at: round.request?.at,
            actor: C.caller,
            type: `${turnLabel(round, tree.rounds)}${C.messageStep}`,
            depth: 0,
            text: round.request?.text,
            empty: C.noRequest,
          },
        ]),
    ...stepsOfItems(round.items, names, cutOff),
    ...(noMessages(round, tree.rounds) && !round.response
      ? []
      : [
          {
            key: `reply-${round.turn}`,
            at: round.response?.at,
            actor: names.coordinator,
            type: `${turnLabel(round, tree.rounds)}${C.replyStep}`,
            depth: 0,
            text: round.response?.text,
            empty: C.noReply,
          },
        ]),
  ]);
};
