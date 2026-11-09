// R4 lane X (8b, 8d): one source for the numbers in every comparison line.
// The Matrix verdicts, the results headline and the Iterations chain all
// take delta, better, worse and carried from runNumbers.compareRuns over the
// same default scope, and print its scopeText. Pure.
import {
  compareRuns,
  rowRangesText,
} from "src/sections/common/Runs/runNumbers";
import { successVerdict } from "src/sections/common/Runs/oneVerdict";
import {
  matrixPairedSuccess,
  withPairedCompare,
} from "src/sections/common/ux/verdictModel";
import { agentVersionOf } from "src/sections/common/Runs/provenanceModel";
import { fmtCount, fmtPts } from "src/sections/common/format/fmt";
import { pageShipLine } from "./pageShip";

/** More worse rows than this (or a drop past LEVEL_DROP_PTS) = warning. */
export const LEVEL_WORSE_MAX = 3;
export const LEVEL_DROP_PTS = -10;

/** The success metric's word in a verdict: `Task success` on a task suite. */
export const successName = (noun) =>
  noun === "task" ? "Task success" : "success";

/**
 * verdictInputs items with their numbers from compareRuns (scope `all`).
 * integration C3 (review 2, M1-residual): when the R3 read was made with this
 * baseline it serves each run's paired verdict (`success_vs_baseline`, the
 * Summary's estimator); the compare then carries it (withPairedCompare), so
 * the Data tab and the Matrix header decide with the Summary's interval.
 * @param {object[]} items  matrixModel.verdictInputs(...)
 * @param {{matrix: object, baselineId: string, noun?: string}} opts
 */
export function scopeVerdictInputs(
  items,
  { matrix, baselineId, noun = "task" },
) {
  if (!matrix || !baselineId) return items ?? [];
  return (items ?? []).map((v) => {
    const compare = withPairedCompare(
      compareRuns({
        matrix,
        baselineId,
        candidateId: v.runId,
        noun,
        candidateEnded: Boolean(v.ended),
      }),
      matrixPairedSuccess(matrix, baselineId, v.runId),
    );
    return {
      ...v,
      compare,
      worse: compare.worse,
      better: compare.better,
      worseRows: compare.worseIds,
      input: {
        ...v.input,
        worse: compare.worse,
        better: compare.better,
        metrics: {
          ...v.input.metrics,
          success: {
            ...v.input.metrics?.success,
            name: v.input.metrics?.success?.name ?? successName(noun),
            noun: v.input.metrics?.success?.noun ?? noun,
            // Confidence counts the tasks both runs ran (the scope's n).
            n: compare.rows.filter((r) => r.change !== "not_run").length,
            cand: compare.candidate.rate,
            base: compare.baseline.rate,
            deltaPts: compare.deltaPts,
            significant: compare.significant,
            ...pairedFieldsOf(compare),
          },
        },
      },
    };
  });
}

/** `{ci95Pts, nPaired}` of a compare that carries a served paired verdict. */
const pairedFieldsOf = (compare) =>
  Number.isFinite(compare?.ci95Pts)
    ? { ci95Pts: compare.ci95Pts, nPaired: compare.nPaired ?? null }
    : {};

/**
 * IT-R1 G2 P1: the verdict of a compareRuns result, for screens that have the
 * compare but no Matrix item (the Experiments headline, the results header).
 * Same sentence as the Matrix: `#2G Support Agent v3.1 beats baseline #2B:
 * +18.8 pts Task success across 30 tasks · improved 8 · worse 2 · same
 * cost · same time. Confidence: …`.
 * @param {object} compare  runNumbers.compareRuns(...)
 * @param {{candidate: {label, name?}, baseline: {label, name?},
 *   noun?: string, tries?: number, costPerTask?: {cand, base},
 *   timePerTask?: {cand, base}}} o
 */
export function verdictOfCompare(compare, o = {}) {
  if (!compare) return null;
  const noun = o.noun ?? "task";
  return scopedVerdict({
    compare,
    input: {
      candidate: o.candidate ?? compare.candidate.label,
      baseline: o.baseline ?? compare.baseline.label,
      worse: compare.worse,
      better: compare.better,
      metrics: {
        success: {
          name: successName(noun),
          noun,
          cand: compare.candidate.rate,
          base: compare.baseline.rate,
          deltaPts: compare.deltaPts,
          significant: compare.significant,
          ...pairedFieldsOf(compare),
          n: compare.rows.filter((r) => r.change !== "not_run").length,
          tries: o.tries,
          candN: compare.candidate.n,
          baseN: compare.baseline.n,
        },
        costPerTask: o.costPerTask,
        timePerTask: o.timePerTask,
      },
    },
  });
}

/**
 * IT-R2 G2 P5: the word for the run a verdict reads against. `baseline`
 * only for the dataset's pinned run; the older version of the same agent is
 * the `previous version`; anything else gets no word (null). A run object
 * without `is_dataset_baseline` keeps `baseline` (nothing to go on).
 */
export function baselineRoleOf(cand, base) {
  if (!base || base.is_dataset_baseline === undefined) return "baseline";
  if (base.is_dataset_baseline) return "baseline";
  const a = cand ? agentVersionOf(cand) : null;
  const b = agentVersionOf(base);
  if (a && b && a.agentKey === b.agentKey && a.version !== b.version)
    return "previous version";
  return null;
}

/** `Partial · 3 of 39 tries scored · verdict when done`. */
export const partialText = (partial) =>
  `Partial · ${partial.scored} of ${fmtCount(partial.total, "try")} scored · verdict when done`;

/**
 * The VerdictLine verdict of a scoped item: the scope after the delta
 * (`… baseline #2B: +20.0 pts Task success across 30 tasks · 24 carried
 * over from #2B · improved 6 · worse 0 …`), `Partial · …` while tries are
 * unscored, and, when a level verdict hides damage (more than 3 tasks worse
 * or a drop past 10 pts), the warning tone with `but N tasks got worse` /
 * `but success dropped …` in the scope.
 */
export function scopedVerdict(v) {
  const compare = v.compare;
  // The counts always come from the compare (one number source).
  // L2 G2 R4: one verdict (oneVerdict.successVerdict) — the compare's own
  // significance (diffRange95 on the tasks both ran) and its one delta.
  const verdict = successVerdict(
    compare
      ? {
          ...v.input,
          better: compare.better,
          worse: compare.worse,
          metrics: {
            ...v.input?.metrics,
            success: {
              ...v.input?.metrics?.success,
              deltaPts: compare.deltaPts,
              significant: compare.significant,
              ...pairedFieldsOf(compare),
            },
          },
        }
      : v.input,
  );
  if (!compare) return verdict;
  // The two run parts (a `Too close to call:` lead puts text first).
  const [cand, base] = verdict.parts.filter((p) => p.t === "run");
  if (compare.partial) {
    const text = partialText(compare.partial);
    const role = v.input?.baselineRole;
    const parts = [
      cand,
      {
        t: "text",
        text:
          role === undefined || role === "baseline" ? " vs baseline " : " vs ",
      },
      base,
      { t: "text", text: ` ${compare.scopeText}: ${text}` },
    ];
    return {
      ...verdict,
      tone: "tie",
      verb: "partial",
      partial: compare.partial,
      chips: [{ key: "partial", label: text, tone: "neutral" }],
      parts,
      sentence: parts.map((p) => p.text).join(""),
      scopeText: compare.scopeText,
    };
  }
  const worse = compare.worse ?? 0;
  const levelButWorse =
    verdict.tone === "tie" &&
    (worse > LEVEL_WORSE_MAX ||
      (Number.isFinite(verdict.deltaPts) && verdict.deltaPts < LEVEL_DROP_PTS));
  const scope = !levelButWorse
    ? ` ${compare.scopeText}`
    : worse > LEVEL_WORSE_MAX
      ? ` ${compare.scopeText}, but ${fmtCount(worse, "task")} got worse`
      : ` ${compare.scopeText}, but success dropped ${fmtPts(verdict.deltaPts)}`;
  const parts = verdict.parts.map((p) =>
    p.role === "scope" ? { ...p, text: scope } : p,
  );
  return {
    ...verdict,
    tone: levelButWorse ? "mixed" : verdict.tone,
    levelButWorse,
    parts,
    sentence: parts.map((p) => p.text).join(""),
    scopeText: compare.scopeText,
  };
}

/**
 * The plain next step after the verdicts, from the same scoped items:
 * `Don't ship #2G v3.1 yet: it fails 6 tasks that #2B passes. Start with T1.`
 * (the worst candidate with worse rows), else `#2D the coding harness Code is ahead and
 * no task got worse: review it, then ship.`; null without a candidate.
 * @param {object[]} items  scopeVerdictInputs(...)
 * @param {{rowLabelOf: (rowId) => string, baseLabel: string, noun?: string}} opts
 */
export function nextStepOf(items, { rowLabelOf, baseLabel, noun = "task" }) {
  const list = (items ?? []).filter((v) => v.compare && !v.compare.partial);
  if (!list.length) return null;
  const name = (v) =>
    [v.label, v.input?.candidate?.name].filter(Boolean).join(" ");
  const worst = [...list]
    .filter((v) => v.compare.worse > 0)
    .sort((a, b) => b.compare.worse - a.compare.worse)[0];
  if (worst) {
    const first = worst.compare.worseIds[0];
    const start = first ? ` Start with ${rowLabelOf(first)}.` : "";
    return `Don't ship ${name(worst)} yet: it fails ${fmtCount(worst.compare.worse, noun)} that ${baseLabel} passes.${start}`;
  }
  const best = [...list].sort(
    (a, b) => (b.compare.deltaPts ?? 0) - (a.compare.deltaPts ?? 0),
  )[0];
  if ((best.compare.deltaPts ?? 0) > 0)
    return `${name(best)} is ahead (${fmtPts(best.compare.deltaPts)}) and no ${noun} got worse: review it, then ship.`;
  return `No run is ahead of ${baseLabel} yet: try a fix on the failing ${noun}s.`;
}

/** Rows of a compare one side did not run, by row number. */
const skippedBy = (compare, who) =>
  (compare?.rows ?? [])
    .filter((r) => r.missing === who || r.missing === "both")
    .map((r) => r.n);

/** `T25–T30`, or `6 tasks` when the rows do not fit three ranges. */
function rangesOrCount(ns, { datasetKind, noun }) {
  const text = rowRangesText(ns, datasetKind);
  return text.split(", ").length <= 3 ? text : fmtCount(ns.length, noun);
}

/**
 * IT-R3 G6 P1 (rule 21): the coverage clause that leads a verdict when the
 * two runs did not run the same rows: `#1D ran 24 of 30 tasks (dataset v1);
 * T25–T30 unknown`. Null when both ran every row in scope.
 * @param {object} compare  runNumbers.compareRuns(...)
 * @param {{candidate?: string, baseline?: string, noun?: string,
 *   datasetKind?: string, candVersion?: number, baseVersion?: number}} o
 */
export function coverageText(compare, o = {}) {
  const total = compare?.rows?.length ?? 0;
  if (!total) return null;
  const noun = o.noun ?? "task";
  const clause = (label, ns, version) =>
    `${label} ran ${total - ns.length} of ${fmtCount(total, noun)}${
      version ? ` (dataset v${version})` : ""
    }; ${rangesOrCount(ns, { datasetKind: o.datasetKind, noun })} unknown`;
  const c = skippedBy(compare, "candidate");
  const b = skippedBy(compare, "baseline");
  const parts = [];
  if (c.length)
    parts.push(
      clause(
        o.candidate ?? compare.candidate?.label ?? "The candidate",
        c,
        o.candVersion,
      ),
    );
  if (b.length)
    parts.push(
      clause(
        o.baseline ?? compare.baseline?.label ?? "The baseline",
        b,
        o.baseVersion,
      ),
    );
  return parts.length ? parts.join(". ") : null;
}

/**
 * IT-R4 G6 P1 (rule 27, S1): the ship / don't-ship line of the PAGE
 * candidate, an adapter over G2's `leaderboardHeadline.shipLine` (the only
 * ship-line builder): `Don't ship the coding harness Code (#2D) yet: it fails T25,
 * which Support Agent v3 (#2B) passes.` (action `Open T25`) / `No task got
 * worse: review it, then ship.` Never the run with the most worse tasks.
 * When the candidate skipped rows the line says so instead of "ship"
 * (`… on the 24 it ran: run it on all 30 before you ship.`). Null without a
 * scored candidate.
 * @param {object[]} items  scopeVerdictInputs(...)
 * @param {{candId?: string|null, rowLabelOf: (rowId) => string,
 *   baseLabel: string, candName?: string, baseName?: string,
 *   noun?: string, datasetKind?: string}} opts  `candName` / `baseName`
 *   = `the coding harness Code (#2D)` (pageShip.whoText); default the labels
 * @returns {{text: string, tone: string, runId: string|null,
 *   rowId: string|null, rowLabel: string|null}|null}
 */
export function shipLineOf(
  items,
  { candId, rowLabelOf, baseLabel, candName, baseName, noun = "task" } = {},
) {
  const list = (items ?? []).filter((v) => v.compare && !v.compare.partial);
  if (!list.length) return null;
  const item = list.find((v) => v.runId === candId) ?? list[0];
  const compare = item.compare;
  const idOfN = new Map((compare.rows ?? []).map((r) => [r.n, r.id]));
  const labelOfN = (n) => {
    const id = idOfN.get(n);
    return (id != null && rowLabelOf?.(id)) || `#${n}`;
  };
  const name =
    candName ??
    [item.label, item.input?.candidate?.name].filter(Boolean).join(" ");
  const s = pageShipLine(compare, {
    cand: name,
    base: baseName ?? baseLabel,
    rowLabel: labelOfN,
    noun,
  });
  if (!s)
    return {
      tone: "tie",
      text: `No run is ahead of ${baseName ?? baseLabel} yet: try a fix on the failing ${noun}s.`,
      runId: null,
      rowId: null,
      rowLabel: null,
    };
  return {
    tone: s.tone,
    text: s.text,
    runId: item.runId ?? null,
    rowId: s.rowId ?? null,
    rowLabel: s.rowText ? s.rowText.replace(/^Open /, "") : null,
  };
}

/** The verdicts + why + next step as one paragraph (`Copy as text`). */
export function verdictsAsText(verdicts, { why, nextStep } = {}) {
  return [
    ...(verdicts ?? []).map((v) => v?.sentence).filter(Boolean),
    why,
    nextStep,
  ]
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * The Iterations chain hop (`#2B → #2F`): the same compareRuns call as the
 * headline. `+20.0 pts across 30 tasks · 24 carried over from #2B ·
 * improved 6 · worse 0` (or `Partial · …`).
 */
export function hopCompare(
  matrix,
  parentId,
  childId,
  { labels, noun = "task" } = {},
) {
  if (!matrix || !parentId || !childId) return null;
  const compare = compareRuns({
    matrix,
    baselineId: parentId,
    candidateId: childId,
    labels,
    noun,
  });
  if (!compare.rows.length) return null;
  const text = compare.partial
    ? `${partialText(compare.partial)} · ${compare.scopeText}`
    : `${fmtPts(compare.deltaPts)} ${compare.scopeText} · improved ${compare.better} · worse ${compare.worse}`;
  return { compare, text };
}
