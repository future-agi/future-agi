import {
  formatMoneyDelta,
  isMoneyValue,
} from "src/sections/common/format/money";
// Rows of the Delta bars chart (W5-6 B5). Pure. Reads R5 `metrics[]`
// (`vs_baseline[{run_id, delta, ci95, significant}]`) exactly as the
// Summary does; only the display unit changes (pass rates in pts).
const MINUS = "−";
const ok = (v) => typeof v === "number" && Number.isFinite(v);
const RATE_TYPES = new Set(["pass_fail", "thumbs"]);

// Monetary text delegates to the shared exact decimal renderer.

const isPts = (m) => m.family === "eval" && RATE_TYPES.has(m.output_type);

function unitText(m, v, { signed = true } = {}) {
  const sign = !signed ? "" : v > 0 ? "+" : v < 0 ? MINUS : "";
  const a = Math.abs(v);
  if (isPts(m)) return `${sign}${a.toFixed(1)} pts`;
  if (/cost/.test(m.key))
    return formatMoneyDelta(v, {
      positiveSign: signed ? "+" : "",
      negativeSign: signed ? MINUS : "",
      zeroSign: "",
      minimumFractionDigits: a < 0.01 ? 0 : 3,
    });
  if (/latency/.test(m.key))
    return `${sign}${Math.round(a).toLocaleString("en-US")} ms`;
  if (/tokens|steps/.test(m.key)) return `${sign}${a.toFixed(1)}`;
  return `${sign}${a.toFixed(3)}`;
}

/**
 * @param {object} analysis  R5 analysis
 * @param {string} candidateId
 * @returns {Array<{key, name, delta, ci95, significant, better, shown,
 *   ciShown, reach, text}>}  `shown` / `ciShown` in the display unit
 */
export function deltaBarRows(analysis, candidateId) {
  const rows = (analysis?.metrics ?? [])
    .filter((m) => ["eval", "ops", "agent"].includes(m.family))
    .map((m) => {
      const vs = (m.vs_baseline ?? []).find((v) => v.run_id === candidateId);
      const monetary = /cost/.test(m.key);
      const valid = monetary ? isMoneyValue : ok;
      if (!vs || !valid(vs.delta)) return null;
      const k = isPts(m) ? 100 : 1;
      const shown = vs.delta * k;
      const ciShown = valid(vs.ci95) ? vs.ci95 * k : null;
      return {
        key: m.key,
        name: m.name,
        pts: isPts(m),
        delta: vs.delta,
        ci95: valid(vs.ci95) ? vs.ci95 : null,
        significant: Boolean(vs.significant),
        better: m.higher_is_better === false ? vs.delta < 0 : vs.delta > 0,
        shown,
        ciShown,
        reach: Math.abs(shown) + (ciShown ?? 0) || 1,
        text: `${unitText(m, monetary ? vs.delta : shown)}${ciShown != null ? ` ± ${unitText(m, monetary ? vs.ci95 : ciShown, { signed: false })}` : ""}${vs.significant ? " · significant" : ""}`,
      };
    })
    .filter(Boolean);
  // Evals in pts share one scale so their bars compare.
  const ptsReach = Math.max(
    1,
    ...rows.filter((r) => r.pts).map((r) => r.reach),
  );
  return rows.map((r) => (r.pts ? { ...r, reach: ptsReach } : r));
}
