// L7c g6: pure cell words. Raw JSON values (view:options): the stored value of a cell as
// JSON, exactly as the API returns it: strings keep their quotes and
// escapes, objects and arrays are not summarised. Pure.
export const isRawFormat = (context) => context?.valueFormat === "raw";

export function rawValueText(value) {
  if (value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/**
 * L7c g6 (cell:trials): the trial dots' words with THIS run's try count
 * (the glossary's generic "attempted 3 times" contradicted 2-, 4- and
 * 5-try runs).
 */
export function trialDotsTip(outcome, metricName) {
  const name = metricName ?? "the pass/fail eval";
  const n = outcome?.n ?? 0;
  return `${outcome?.pass ?? 0} of ${n} trials passed on ${name}. A trial is one try of one task; this run tries each task ${n} ${n === 1 ? "time" : "times"}.`;
}
