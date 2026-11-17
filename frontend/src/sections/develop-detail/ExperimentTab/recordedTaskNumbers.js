// The Experiments list's Cost per task and Time (p50) columns, from what the
// results matrix records for each try: `cost_usd` and `wall_ms`, by try, in
// the order of `trace_summary`. Pure.
// - A try counts when it ran: completed, errored, or failed on
//   infrastructure. A queued, stopped or never-reached try did not run and
//   counts for neither column.
// - A task counts when at least one of its tries ran.
// - A number is given only when every try that ran has its value recorded
//   (the server's own rule for a run's totals, native_run_projection.py).
//   Otherwise the cell says why, in these words.

const RAN = new Set(["completed", "error", "infra"]);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export const RECORDED_REASON = Object.freeze({
  noTries: "No finished tries yet",
  noneRan: "No try of this run ran",
  perTaskNone: "No cost recorded for this run's tries",
  perTaskSome: "Cost not recorded for every try that ran",
  perTaskZero: "Every try that ran recorded $0",
  timeNone: "No time recorded for this run's tries",
  timeSome: "Time not recorded for every try that ran",
  partial: "More tasks than one read holds, so not totalled here",
  // A finished run the results read did not cover: no number is shown, and
  // the run's own summary fields are not read (the run list sends none).
  notRead: "Not read: this run has no recorded plan.",
  readFailed: "Could not read this run's results. Try again.",
});

/** The few visible words for each reason (the full reason is on hover). */
export const RECORDED_SHORT = Object.freeze({
  [RECORDED_REASON.noneRan]: "No tries ran",
  [RECORDED_REASON.perTaskNone]: "Not recorded",
  [RECORDED_REASON.perTaskSome]: "Not recorded",
  [RECORDED_REASON.perTaskZero]: "$0 recorded",
  [RECORDED_REASON.timeNone]: "Not recorded",
  [RECORDED_REASON.timeSome]: "Not recorded",
  [RECORDED_REASON.partial]: "Too many tasks",
  [RECORDED_REASON.notRead]: "Not read",
  [RECORDED_REASON.readFailed]: "Could not read",
});

/** The median of finite numbers (the mean of the middle two when even). */
export function median(values) {
  const v = (values ?? []).filter(isNum).sort((a, b) => a - b);
  if (!v.length) return null;
  const mid = Math.floor(v.length / 2);
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

/**
 * One run's recorded numbers from a matrix that holds it.
 * @param {object} matrix  a results `shape=matrix` read (or merged reads)
 * @param {string} runId
 * @param {{partial?: boolean}} [o]  the run's read had more rows than it
 *   returned (`pagination.has_more`)
 * @returns {{tasks: number, tries: number,
 *   perTask: {usd: number|null, total: number|null, reason: string|null},
 *   time: {ms: number|null, reason: string|null}}}
 */
export function recordedTaskNumbers(matrix, runId, { partial = false } = {}) {
  let tasks = 0;
  let tries = 0;
  let priced = 0;
  let total = 0;
  const walls = [];
  (matrix?.rows ?? []).forEach((row) => {
    const cell = row?.cells?.[runId];
    if (!cell || Array.isArray(cell)) return;
    const summary = Array.isArray(cell.trace_summary) ? cell.trace_summary : [];
    let ran = false;
    summary.forEach((t, i) => {
      if (!RAN.has(t?.status)) return;
      ran = true;
      tries += 1;
      const usd = cell.cost_usd?.[i];
      if (isNum(usd)) {
        priced += 1;
        total += usd;
      }
      const ms = cell.wall_ms?.[i];
      if (isNum(ms)) walls.push(ms);
    });
    if (ran) tasks += 1;
  });
  const none = (reason) => ({ usd: null, total: null, ms: null, reason });
  if (partial)
    return {
      tasks,
      tries,
      perTask: none(RECORDED_REASON.partial),
      time: none(RECORDED_REASON.partial),
    };
  if (!tries)
    return {
      tasks,
      tries,
      perTask: none(RECORDED_REASON.noTries),
      time: none(RECORDED_REASON.noTries),
    };
  const perTask =
    priced === 0
      ? none(RECORDED_REASON.perTaskNone)
      : priced < tries
        ? none(RECORDED_REASON.perTaskSome)
        : total === 0
          ? none(RECORDED_REASON.perTaskZero)
          : { usd: total / tasks, total, ms: null, reason: null };
  const time =
    walls.length === 0
      ? none(RECORDED_REASON.timeNone)
      : walls.length < tries
        ? none(RECORDED_REASON.timeSome)
        : { usd: null, total: null, ms: median(walls), reason: null };
  return { tasks, tries, perTask, time };
}
