// L2 G2 R4 (quality consistency #1, #2, #3): one verdict for a candidate
// against a baseline on every screen — the Experiments headline, Compare
// Summary, Compare Data, the Matrix header and the dataset Summary tab.
//
// - Significance is ONE rule: the 95% range of the success difference on
//   the tasks both ran (runNumbers.diffRange95, the Experiments headline's
//   range) must not hold 0. Two runs whose range crosses 0 are `Too close
//   to call` everywhere and never `beats`, whatever the try counts say.
// - The success delta is rounded once from the unrounded rates
//   (runNumbers.ptsDelta: +18.9 pts, never +18.8 from two rounded
//   percents).
// - A not-significant verdict reads `Too close to call: #2G … vs baseline
//   #2B, +18.9 pts Task success (within noise) …`; never `is level with`.
// Pure: no React. verdictOf (common/ux) still builds the sentence shape.
import { verdictOf } from "src/sections/common/ux/verdictModel";
import { diffRange95, ptsDelta } from "./runNumbers";

export const TOO_CLOSE = "Too close to call";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/**
 * The significance of a success difference: an explicit boolean wins (the
 * compare's own `significant`), else diffRange95 over `n` tasks.
 * @returns {{significant: boolean, range: object|null}}
 */
export function successSignificance({ cand, base, n, significant } = {}) {
  const range = diffRange95(cand, base, n);
  if (typeof significant === "boolean") return { significant, range };
  return { significant: Boolean(range?.real), range };
}

/**
 * A verdictOf result whose tone is `tie`, rewritten to lead with `Too close
 * to call:` — `Too close to call: #2G Support Agent v3.1 vs baseline #2B,
 * +18.9 pts Task success (within noise) across 30 tasks · …`. Other tones
 * are returned unchanged. `verb` keeps verdictOf's key.
 */
export function tooCloseWording(verdict) {
  if (!verdict || verdict.tone !== "tie" || !Array.isArray(verdict.parts))
    return verdict;
  const [cand, verbPart, base, sep, ...rest] = verdict.parts;
  if (
    cand?.t !== "run" ||
    verbPart?.t !== "text" ||
    !/is level with/.test(verbPart.text ?? "")
  )
    return verdict;
  const vs = /baseline/.test(verbPart.text) ? " vs baseline " : " vs ";
  const parts = [
    { t: "text", text: `${TOO_CLOSE}: ` },
    cand,
    { t: "text", text: vs },
    base,
    sep?.text === ": " ? { ...sep, text: ", " } : sep,
    ...rest,
  ].filter(Boolean);
  return { ...verdict, parts, sentence: parts.map((p) => p.text).join("") };
}

/**
 * verdictOf with the one significance rule and the one delta. Same input
 * as verdictOf; `metrics.success.n` counts the tasks both ran.
 * @param {object} input  verdictOf input
 * @returns {object} verdictOf result + `range` (diffRange95)
 */
export function successVerdict(input = {}) {
  const metrics = input.metrics ?? {};
  const success = metrics.success ?? {};
  const { significant, range } = successSignificance(success);
  const deltaPts = isNum(success.deltaPts)
    ? success.deltaPts
    : ptsDelta(success.cand, success.base);
  const v = verdictOf({
    ...input,
    metrics: {
      ...metrics,
      success: {
        ...success,
        significant,
        ...(isNum(deltaPts) ? { deltaPts } : {}),
      },
    },
  });
  return { ...tooCloseWording(v), range };
}
