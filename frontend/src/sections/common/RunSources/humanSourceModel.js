// W6 C5 (W6-SCOPE.md §9 C5): a human baseline, pure. The People source
// assigns a run's rows to workspace members; each answer becomes the try's
// output (latency = the time the person took) and is scored by the run's
// evals like any other try. This module holds the plan and the numbers both
// the wizard and the mock world read; nothing here is prototype-only.
//
// - assignRows: "split" gives each row to one person, round robin in row
//   order; "overlap" gives each row to `overlap` people (rotating), so rows
//   are answered k times and agreement can be read.
// - The time limit and the instructions are the user's own values; a run
//   without a time limit has none (no default is invented here).

export const HUMAN_KIND = "human";
export const HUMAN_MODES = Object.freeze(["split", "overlap"]);

const idOf = (p) => (typeof p === "string" ? p : p?.id ?? p?.user_id ?? null);

/**
 * Row → person assignments: `[{row_id, assignee}]` in row order.
 * @param {string[]} rowIds  the run's rows, in order
 * @param {(string|{id: string})[]} assignees  workspace members
 * @param {{mode?: "split"|"overlap", overlap?: number}} [opts]
 */
export function assignRows(rowIds = [], assignees = [], opts = {}) {
  const people = assignees.map(idOf).filter(Boolean);
  if (!people.length) return [];
  const mode = opts.mode === "overlap" ? "overlap" : "split";
  const k =
    mode === "overlap"
      ? Math.min(
          people.length,
          Math.max(1, Math.floor(Number(opts.overlap) || 1)),
        )
      : 1;
  const out = [];
  rowIds.forEach((rowId, i) => {
    for (let j = 0; j < k; j += 1)
      out.push({ row_id: rowId, assignee: people[(i + j) % people.length] });
  });
  return out;
}

/** Rows per person: `{[assignee]: n}`. */
export function rowsPerPerson(assignments = []) {
  return assignments.reduce((acc, a) => {
    acc[a.assignee] = (acc[a.assignee] ?? 0) + 1;
    return acc;
  }, {});
}

/** `Humans (3 people)` / `Humans (1 person)`. */
export const humanRunName = (assignees = []) => {
  const n = assignees.map(idOf).filter(Boolean).length;
  return `Humans (${n} ${n === 1 ? "person" : "people"})`;
};

/** The form's validation: `{valid, field, reason}`. */
export function validateHuman(h = {}, { rows = 0, members = [] } = {}) {
  const people = (h.assignees ?? []).map(idOf).filter(Boolean);
  const known = new Set(members.map(idOf));
  if (!people.length)
    return {
      valid: false,
      field: "assignees",
      reason: "Pick at least one teammate",
    };
  const outsider = people.find((p) => known.size && !known.has(p));
  if (outsider)
    return {
      valid: false,
      field: "assignees",
      reason: "Only workspace members can answer rows",
    };
  if (h.mode === "overlap") {
    const k = Number(h.overlap);
    if (!Number.isInteger(k) || k < 2 || k > people.length)
      return {
        valid: false,
        field: "overlap",
        reason: `Overlap is a whole number from 2 to ${people.length}`,
      };
  }
  if (h.time_limit_s != null && h.time_limit_s !== "") {
    const t = Number(h.time_limit_s);
    if (!Number.isFinite(t) || t <= 0)
      return {
        valid: false,
        field: "time_limit_s",
        reason: "A time limit is a number of seconds above 0",
      };
  }
  if (!rows)
    return {
      valid: false,
      field: "rows",
      reason: "The run has no rows to assign",
    };
  return { valid: true, field: null, reason: null };
}

/** The create body's `human` block. */
export function humanBodyOf(h = {}) {
  const t = Number(h.time_limit_s);
  return {
    assignees: (h.assignees ?? []).map(idOf).filter(Boolean),
    mode: h.mode === "overlap" ? "overlap" : "split",
    overlap: h.mode === "overlap" ? Number(h.overlap) : 1,
    time_limit_s: Number.isFinite(t) && t > 0 ? t : null,
    instructions: String(h.instructions ?? "").trim(),
  };
}

/** `12 of 40 rows answered` (assignments answered of assigned). */
export const progressLine = (answered, assigned) =>
  `${answered} of ${assigned} row${assigned === 1 ? "" : "s"} answered`;

/** The median of the answer times (ms), or null with none. */
export function p50Ms(values = []) {
  const xs = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

/** Whether an answer came in within the run's time limit. */
export const withinLimit = (elapsedMs, timeLimitS) =>
  timeLimitS == null || elapsedMs <= timeLimitS * 1000;
