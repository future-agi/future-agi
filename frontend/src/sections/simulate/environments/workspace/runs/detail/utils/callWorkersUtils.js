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
 * Grouping only: which hand-off owns what, spans, depth and parallel runs all come from the API.
 */
export const nest = (data) => {
  const byKey = {};
  const items = [];
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
            items: [],
          }
        : { kind: "call", key: step.key, event: step.call };
    if (row.kind === "handoff") byKey[step.key] = row;
    (step.parent === null ? items : byKey[step.parent]?.items || items).push(
      row,
    );
  }
  return {
    items,
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

const toolEvent = (event, depth, names) => ({
  key: `call-${event.seq}`,
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
      return [toolEvent(item.event, block.depth, names)];
    return nested ? stepsOf(item, names, cutOff, true) : [];
  }),
  ...endEvents(block, names, cutOff),
];

/** One hand-off's own steps, leaving out the hand-offs it made, which have their own table rows. */
export const handOffEvents = (block, names, cutOff) =>
  stepsOf(block, names, cutOff, false);

/** Every step of a call in reading order, between the caller's request and the reply. */
export const callEvents = (data, tree, names, cutOff, hasHandOffs) => {
  const steps = tree.items.flatMap((item) =>
    item.kind === "call"
      ? [toolEvent(item.event, 0, names)]
      : stepsOf(item, names, cutOff, true),
  );
  if (!hasHandOffs) return steps;
  return [
    {
      key: "request",
      at: data.request?.at,
      actor: C.caller,
      type: C.messageStep,
      depth: 0,
      text: data.request?.text,
      empty: C.noRequest,
    },
    ...steps,
    {
      key: "reply",
      at: data.response?.at,
      actor: names.coordinator,
      type: C.replyStep,
      depth: 0,
      text: data.response?.text,
      empty: C.noReply,
    },
  ];
};
