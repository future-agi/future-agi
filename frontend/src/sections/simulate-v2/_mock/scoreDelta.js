/*
  How a score moved, said one way everywhere (compare, the runs list).

  Scores and pass rates are 0–100 points, and a change in them is always the
  absolute difference in points: 80 → 98 is "+18", 1 → 58 is "+57". It is never
  a percent of the old value ("+1000%", "+22%"), which says nothing a reader can
  act on and is undefined when the old value is 0.
*/

const num = (v) =>
  v == null || v === "" || Number.isNaN(Number(v)) ? null : Number(v);

/** A 0–1 share as whole points (0.98 → 98); null stays null. */
export const toPoints = (share) => {
  const n = num(share);
  return n == null ? null : Math.round(n * 100);
};

/** now − before in whole points; null when either side has no number. */
export const pointsDelta = (now, before) => {
  const a = num(now);
  const b = num(before);
  if (a == null || b == null) return null;
  return Math.round(a) - Math.round(b);
};

/** "+18", "−5", "0" — the minus is a real minus sign. */
export const signedPoints = (d) => {
  if (d == null) return "";
  if (d > 0) return `+${d}`;
  if (d < 0) return `−${Math.abs(d)}`;
  return "0";
};

/**
 * One reading of a change for display:
 * { pts, dir: "up"|"down"|"same", arrow: "▲"|"▼"|"", text: "+18", better: true|false|null }.
 * `better` is null when nothing moved; `lowerIsBetter` flips it for counts like
 * "turns". Null when there is nothing to compare against.
 */
export const readDelta = (now, before, { lowerIsBetter = false } = {}) => {
  const pts = pointsDelta(now, before);
  if (pts == null) return null;
  const dir = pts > 0 ? "up" : pts < 0 ? "down" : "same";
  return {
    pts,
    dir,
    arrow: dir === "up" ? "▲" : dir === "down" ? "▼" : "",
    text: dir === "same" ? "" : signedPoints(pts),
    better:
      dir === "same" ? null : lowerIsBetter ? dir === "down" : dir === "up",
  };
};

/** Smallest change that is more than noise, by kind: eval scores in points; counts (sub-goals, trials) by 1. */
export const NOISE_THRESHOLD = { score: 5, count: 1 };

/**
 * Is a change a real one? `delta` is a readDelta() result or a plain number of
 * points. Eval scores averaged over few conversations move by chance, so under
 * 5 points is noise; sub-goal and trial counts count from 1.
 */
export const isMeaningful = (delta, kind = "score") => {
  const pts = typeof delta === "number" ? delta : delta?.pts;
  if (pts == null || Number.isNaN(pts)) return false;
  return Math.abs(pts) >= (NOISE_THRESHOLD[kind] ?? NOISE_THRESHOLD.score);
};

/** "Run 5" — an epoch's run is "Epoch 1 · Candidate 2" (the epoch, then which of its candidates); another environment's says where. */
export const runLabelOf = (run) => {
  if (!run) return "";
  const base =
    run.kind === "trial" || run.trialN
      ? `${run.selfImprovementName || "Epoch"} · Candidate ${run.trialN}`
      : `Run ${run.ordinal ?? ""}`.trim();
  return run._env && run.envName ? `${base} · ${run.envName}` : base;
};

/** The line above the compare table: which run every arrow is read against. */
export const comparedWithText = (runs) =>
  runs && runs.length ? `Compared with ${runLabelOf(runs[0])} (baseline)` : "";

/** The first run is the baseline; the rest are labelled by their own number. */
export const rowLabelOf = (run, index) =>
  index === 0 ? "Baseline" : runLabelOf(run);
