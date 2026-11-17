// L7c g6 (status:ledger): the errored tries of the runs in view, one count
// per error policy, for the results footer's plain-text count. Pure.

/**
 * @param {{label?: string, errored_tries?: number, error_policy?: string}[]} runs
 * @returns {{zero: number, leftOut: number, title: string|null}}
 *   zero = tries counted as 0 (a fail), leftOut = tries left out of scores,
 *   title = the per-run counts (`#2A 1 · #2E 2`) with the rule in words
 */
export function erroredTriesOf(runs) {
  let zero = 0;
  let leftOut = 0;
  const zeroRuns = [];
  const outRuns = [];
  (runs ?? []).forEach((r) => {
    const n = Number(r?.errored_tries) || 0;
    if (n <= 0) return;
    const part = `${r.label ?? r.run_id ?? "run"} ${n}`;
    if (r.error_policy === "exclude") {
      leftOut += n;
      outRuns.push(part);
    } else {
      zero += n;
      zeroRuns.push(part);
    }
  });
  const lines = [
    zeroRuns.length
      ? `Errored tries count as 0 (a fail) in every score: ${zeroRuns.join(" · ")}.`
      : null,
    outRuns.length
      ? `Errored tries are left out of every score: ${outRuns.join(" · ")}.`
      : null,
  ].filter(Boolean);
  return { zero, leftOut, title: lines.length ? lines.join(" ") : null };
}

const triesWord = (n) => `${n} errored ${n === 1 ? "try" : "tries"}`;

/**
 * `3 errored tries counted as 0` / `2 errored tries left out of scores`,
 * one clause per error policy in view (the footer's plain-text count).
 */
export function erroredTriesText(zero = 0, leftOut = 0) {
  return [
    zero > 0 ? `${triesWord(zero)} counted as 0` : null,
    leftOut > 0 ? `${triesWord(leftOut)} left out of scores` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}
