import { formatExactMoney } from "src/sections/common/format/money";
// Isolate one change (R2-SPEC §3.3.3). Pure.
//
// When a baseline and a candidate differ on more than one axis, each changed
// axis gets one line: an existing run on the dataset that changes ONLY that
// axis (and takes the candidate's value for it) answers "what did that one
// change do?"; else the line offers to run it.
import { estimateRun } from "src/sections/common/Analysis/runStats";
import { configDiff } from "./configDiff";
import { runLabel } from "./runLabel";

const AXIS_TITLE = {
  prompt: "Prompt",
  model: "Model",
  params: "Params",
  tools: "Tools",
  agent: "Agent",
  code: "Code",
  traces: "Traces",
  harness: "Harness",
  dataset: "Dataset",
  evals: "Evals",
  trials: "Trials",
};

const axesOf = (chips) => [...new Set(chips.map((c) => c.axis))];

/** The run's main eval aggregate (first `eval:*` key, else the first). */
export function mainAggregate(run, key) {
  const aggs = run?.metrics?.aggregates ?? {};
  const k =
    key ??
    Object.keys(aggs).find((x) => x.startsWith("eval:")) ??
    Object.keys(aggs)[0];
  return k ? { key: k, agg: aggs[k] ?? null } : { key: null, agg: null };
}

/** Points of `run` vs `baseline` on `key`: the API's `delta` vs the pinned baseline, else the avg difference. */
export function pointsVs(run, baseline, key) {
  const a = run?.metrics?.aggregates?.[key];
  const b = baseline?.metrics?.aggregates?.[key];
  if (!a) return { pts: null, significant: null };
  if (baseline?.is_dataset_baseline && Number.isFinite(a.delta))
    return { pts: a.delta * 100, significant: a.significant ?? null };
  if (Number.isFinite(a.avg) && Number.isFinite(b?.avg))
    return { pts: (a.avg - b.avg) * 100, significant: null };
  return { pts: null, significant: null };
}

const signed = (x) =>
  `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(1)}`;

/** The from → to words of an axis (`V3 → V4`, `gpt-4o-mini → claude-sonnet-4-5`). */
function fromTo(axis, chips) {
  const c = chips.find((x) => x.axis === axis);
  const label = c?.label ?? "";
  const body = label.replace(new RegExp(`^${axis}\\s+`, "i"), "");
  return body.replace(/\s*\(.*\)$/, "");
}

/**
 * Isolate lines for a baseline / candidate pair.
 * @param {object} baseline  R1 run with `config`, `metrics`
 * @param {object} candidate
 * @param {object[]} runs  R1 runs on the dataset
 * @returns {{changed: number, options: Array<{axis, title, run?, text,
 *   action: "compare"|"run", estimate?}>}}
 */
export function isolateOptions(baseline, candidate, runs) {
  if (!baseline?.config || !candidate?.config)
    return { changed: 0, options: [] };
  const chips = configDiff(baseline, candidate);
  if (chips.some((c) => c.axis === "kind")) return { changed: 0, options: [] };
  const axes = axesOf(chips).filter((a) => a !== "dataset");
  if (axes.length < 2) return { changed: axes.length, options: [] };
  const { key } = mainAggregate(candidate);
  const total = pointsVs(candidate, baseline, key).pts;
  const options = axes.map((axis) => {
    const title = AXIS_TITLE[axis] ?? axis;
    const single = (runs ?? []).find((r) => {
      if (
        !r?.config ||
        r.run_id === baseline.run_id ||
        r.run_id === candidate.run_id
      )
        return false;
      const vsBase = axesOf(configDiff(baseline, r)).filter(
        (a) => a !== "dataset",
      );
      const vsCand = axesOf(configDiff(r, candidate)).filter(
        (a) => a !== "dataset",
      );
      return (
        vsBase.length === 1 && vsBase[0] === axis && !vsCand.includes(axis)
      );
    });
    if (single) {
      const { pts, significant } = pointsVs(single, baseline, key);
      const others = axes
        .filter((a) => a !== axis)
        .map((a) => (AXIS_TITLE[a] ?? a).toLowerCase());
      const noise = significant === false ? " (within noise)" : "";
      const rest =
        Number.isFinite(total) && others.length
          ? ` The ${others.join(" and ")} change carries most of the ${signed(total)} pts; run "${others[0]} only" to confirm.`
          : "";
      return {
        axis,
        title,
        run: single,
        action: "compare",
        text: Number.isFinite(pts)
          ? `${title} ${fromTo(axis, chips)} alone: ${runLabel(single)}, ${signed(pts)} pts${noise}.${rest}`
          : `${title} ${fromTo(axis, chips)} alone: ${runLabel(single)}.`,
      };
    }
    const rows =
      baseline.examples ??
      baseline.config?.dataset?.rows ??
      baseline.progress?.total ??
      0;
    // Cost per attempt from the run that already used this value (the
    // candidate for a model change, else the baseline).
    const priced = axis === "model" ? candidate : baseline;
    const aggs = priced.metrics?.aggregates ?? {};
    const est = estimateRun({
      rows,
      trials: baseline.config?.trials ?? baseline.trials ?? 1,
      targets: [
        {
          key: priced.run_id,
          model: priced.config?.model?.id,
          cost_per_attempt: aggs["ops:cost"]?.avg,
          p50_ms: aggs["ops:latency"]?.median ?? aggs["ops:latency"]?.avg,
        },
      ],
      evals: [],
    });
    return {
      axis,
      title,
      action: "run",
      estimate: est,
      text: `${title} only: not run yet`,
      estimateText: `≈ ${formatExactMoney(est.total_usd)} · about ${est.seconds} s`,
    };
  });
  return { changed: axes.length, options };
}
