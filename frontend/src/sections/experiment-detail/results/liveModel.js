// Live results while a run in view is still going (iterate r2 G3 P5/P7).
// Pure. `Live · 12 of 30 scored · about 40 s left` from each run's own
// `progress` (scored / total / eta_s, the experiment sim), never a local
// estimate; `9 new results` = what landed since the view was paused.

const DONE = new Set(["Completed", "Cancelled", "Failed", "PartialCompleted"]);

/** The runs in view still scoring. */
export const liveRuns = (runs) =>
  (runs ?? []).filter(
    (r) =>
      r &&
      !r.not_found &&
      r.progress &&
      !DONE.has(r.progress.status ?? r.status) &&
      (r.progress.status ?? r.status) !== "WaitingForResults",
  );

/**
 * @param {object[]} runs  summary.runs
 * @returns {{scored: number, total: number, eta: number|null, label: string|null}|null}
 */
export function liveProgress(runs) {
  const live = liveRuns(runs);
  if (!live.length) return null;
  // S3: the same count the run chip prints (`done/total`), so the chip
  // and this counter never disagree (`1/24` next to `0/24`).
  const scored = live.reduce(
    (a, r) => a + (r.progress.done ?? r.progress.scored ?? 0),
    0,
  );
  const total = live.reduce((a, r) => a + (r.progress.total ?? 0), 0);
  const etas = live
    .map((r) => r.progress.eta_s)
    .filter((v) => Number.isFinite(v));
  return {
    scored,
    total,
    eta: etas.length ? Math.max(...etas) : null,
    label: live.length === 1 ? live[0].label ?? null : null,
  };
}

/** Results scored in `now` that `then` (a paused snapshot) did not have. */
export function newResultsSince(thenRuns, nowRuns) {
  const scoredOf = (runs, id) =>
    (runs ?? []).find((r) => r.run_id === id)?.progress?.scored ?? 0;
  return (nowRuns ?? []).reduce(
    (a, r) =>
      a +
      Math.max(0, scoredOf(nowRuns, r.run_id) - scoredOf(thenRuns, r.run_id)),
    0,
  );
}

/** `about 40 s left` / `about 2 min left` / `finishing`. */
export function etaText(seconds) {
  if (!Number.isFinite(seconds)) return null;
  if (seconds <= 3) return "finishing";
  if (seconds < 90) return `about ${Math.round(seconds / 5) * 5 || 5} s left`;
  return `about ${Math.round(seconds / 60)} min left`;
}

/**
 * iterate r2 G3 (P5): a fix-run (an iteration of the baseline, or of one of
 * its fix-runs) sits right after the baseline, newest first, so it never
 * lands off-screen right. Other runs keep their order.
 */
export function fixRunsNextToBaseline(runs, baselineId) {
  const list = runs ?? [];
  if (!baselineId || list.length < 3) return list;
  const byId = new Map(list.map((r) => [r.run_id, r]));
  const descends = (r) => {
    let cur = r;
    for (let i = 0; i < 8 && cur?.parent_run_id; i += 1) {
      if (cur.parent_run_id === baselineId) return true;
      cur = byId.get(cur.parent_run_id);
    }
    return false;
  };
  const base = list.filter((r) => r.run_id === baselineId);
  const fixes = list
    .filter((r) => r.run_id !== baselineId && descends(r))
    .reverse();
  const rest = list.filter(
    (r) => r.run_id !== baselineId && !fixes.includes(r),
  );
  if (!base.length || !fixes.length) return list;
  const baseIndex = list.findIndex((r) => r.run_id === baselineId);
  const before = rest.filter((r) => list.indexOf(r) < baseIndex);
  const after = rest.filter((r) => list.indexOf(r) > baseIndex);
  return [...before, ...base, ...fixes, ...after];
}

/**
 * IT-R5 G3 (P1, seam S3): ONE run progress object. The run chips and the
 * live counter read each run's `progress` from the live summary (the same
 * query), even while the rows are paused, and a count never runs past the
 * run's own total or the rows that exist. Pure.
 * @param {object[]} runs  the runs the grid shows (maybe a paused snapshot)
 * @param {object[]} liveRuns  the live summary's runs
 * @param {number} [rowsTotal]  rows the grid holds (examples in view)
 */
export function withLiveProgress(runs, liveRuns, rowsTotal) {
  const byId = new Map((liveRuns ?? []).map((r) => [r.run_id, r]));
  return (runs ?? []).map((r) => {
    const p = byId.get(r.run_id)?.progress ?? r.progress;
    if (!p) return r;
    const cap = Math.min(
      Number.isFinite(p.total) ? p.total : Infinity,
      Number.isFinite(rowsTotal) && rowsTotal > 0 ? rowsTotal : Infinity,
    );
    const clamp = (v) =>
      Number.isFinite(v) && Number.isFinite(cap) ? Math.min(v, cap) : v;
    const total = Number.isFinite(cap) ? cap : p.total;
    return {
      ...r,
      progress: {
        ...p,
        total,
        done: clamp(p.done),
        scored: clamp(p.scored),
      },
    };
  });
}
