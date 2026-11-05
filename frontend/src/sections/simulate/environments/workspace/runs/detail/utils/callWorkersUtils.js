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
    (step.parent === null ? items : byKey[step.parent]?.items || items).push(
      row,
    );
  }
  // One block per turn the API returned, each with the top-level rows that happened in it.
  const fallback = data.steps?.length
    ? [
        {
          turn: 0,
          request: data.request ?? null,
          response: data.response ?? null,
        },
      ]
    : [];
  const rounds = (data.turns?.length ? data.turns : fallback).map((round) => ({
    ...round,
    items: [],
  }));
  const roundOf = Object.fromEntries(
    rounds.map((round) => [round.turn, round]),
  );
  for (const step of data.steps || []) {
    if (step.parent !== null) continue;
    const row = items.find((one) => one.key === step.key);
    (roundOf[step.turn] ?? rounds[rounds.length - 1]).items.push(row);
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
