import { formatExactMoney } from "src/sections/common/format/money";
// W1-2: the words every re-score sentence is built from (rescoreCopyModel
// and rescoreMessage). Pure.

export const joinAnd = (xs) =>
  xs.length <= 1
    ? xs[0] ?? ""
    : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

export const n = (x) => Number(x || 0).toLocaleString("en-US");
export const plural = (k, one, many = `${one}s`) =>
  `${n(k)} ${k === 1 ? one : many}`;
export const keepScores = (labels) =>
  `${joinAnd(labels)} ${labels.length === 1 ? "keeps its" : "keep their"} scores.`;

/** `$0.45` · `$0.004` · `$0` (the estimator's cost words). */
export function usdText(v) {
  const a = Math.abs(Number(v));
  return formatExactMoney(v, {
    minimumFractionDigits: a < 0.001 ? 0 : a < 0.1 ? 3 : 2,
  });
}

/** Read the re-score result out of an axios response, a body or a result. */
export function rescoreResultOf(response) {
  const r = response?.data?.result ?? response?.result ?? response;
  return r && typeof r === "object" ? r : null;
}

/** Runs the re-score could not touch: "#2A·2 is still running; …". */
export const skippedText = (skipped = []) =>
  skipped.length
    ? ` ${skipped.map((s) => `${s.label} ${/^still/.test(s.reason) ? "is " : ""}${s.reason}.`).join(" ")}`
    : "";
