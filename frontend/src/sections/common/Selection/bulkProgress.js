// W5-7 (C3): the words of a long bulk action's progress line
// (`Exporting 1,200 of 4,800 rows…`). The counts are the action's own
// (rows read so far, the view's total); nothing here guesses a size.

const fmt = (n) => Math.max(0, Math.round(n)).toLocaleString("en-US");

/**
 * @param {{doing: string, done: string, count: number, total?: number,
 *   noun?: string}} p  doing = `Exporting`, done = `Exported`
 * @returns {string}
 */
export function progressText({ doing, done, count, total, noun = "row" }) {
  const known = Number.isFinite(total) && total >= 0;
  const many = (n) => `${noun}${n === 1 ? "" : "s"}`;
  if (known && count >= total) return `${done} ${fmt(total)} ${many(total)}`;
  if (!known) return `${doing} ${fmt(count)} ${many(count)}…`;
  return `${doing} ${fmt(count)} of ${fmt(total)} ${many(total)}…`;
}

/** 0..100 for a determinate bar, or null when the total is unknown. */
export function progressPercent(count, total) {
  if (!Number.isFinite(total) || total <= 0) return null;
  return Math.min(100, Math.max(0, (count / total) * 100));
}
