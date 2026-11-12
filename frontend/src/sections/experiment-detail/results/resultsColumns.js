// Column model of the results grid (PRODUCT-SPEC §3.10.7). Pure: it turns
// the R3 `summary` and the view state into AG Grid column defs whose
// renderers are registered by name in RunResultsView (`components`).
//
//   Row · Splits · Input · Expected            once (Row and Input pinned)
//   Output                                     one child per run
//   one group per eval / human label           one child per run, baseline first
//   Preference                                 Human · LLM judge (≥ 2 runs)
//   Latency · Cost · Tokens (hidden) · Steps   one child per run
//   Tool calls · Tool errors · Turns (R2, off by default) one child per run
//
// Trace roll-ups (R2-SPEC §5.2): `Steps`, `Tool calls`, `Tool errors`,
// `Turns`, `Cost`, `Wall time`. Steps, Cost and Wall time are the existing
// columns (agent:steps, ops:cost, ops:latency); the rest are new opt-in
// columns read from each result's `trace_summary[]`.
import { ANY_CANDIDATE } from "src/sections/common/Results/filterPills";
import {
  EMPTY,
  fmtCost,
  fmtCount,
  fmtMs,
  fmtNumber,
  fmtPct,
} from "src/sections/common/format/fmt";
import { fmtLatency } from "src/sections/common/Analysis/voiceSummaryModel";
import { formatValue } from "src/sections/common/Metrics/metricFormat";
import {
  CUSTOM_GROUP,
  customCatalogEntries,
  pushCustomColumns,
} from "src/sections/common/CustomColumns/resultsCustomColumns";
import {
  rateInterval,
  reducerLabel,
} from "src/sections/common/Analysis/trialReducers";
import {
  mixedNote,
  notComparedNote,
  rescoreTargetOf,
} from "./headers/evalVersionText";

export const EXAMPLE_COLUMNS = [
  { key: "splits", label: "Splits" },
  { key: "expected", label: "Expected" },
  { key: "meta", label: "Metadata" },
];

export const OUTPUT_KEY = "output";
export const PREFERENCE_KEY = "preference";
// R3 (SVP-4.7): Splits hidden by default (still filterable from Filter).
export const DEFAULT_HIDDEN = ["ops:tokens", "meta", "splits"];

export const colIdFor = (key, runId) => (runId ? `${key}|${runId}` : key);

// ------------------------------------------------------------ R4 voice
// MUST 5: per-call voice columns on scenarios datasets, from the
// voice summary (voiceSummaryModel.callStats). Default: Task completion,
// Duration, Latency p50, Latency p90, Kept talking, Cost per call; `Columns`
// adds WER and Talk ratio. Text-only runs show `—` with the reason.
const TEXT_ONLY_WHY = "Text only · no audio, so no";
export const VOICE_COLUMNS = Object.freeze([
  {
    key: "voice:completion",
    label: "Task completion",
    get: (c) => (c.passed ? 1 : 0),
    fmt: (v) => (v ? "Completed" : "Not completed"),
    text: true,
  },
  {
    key: "voice:duration",
    label: "Duration",
    get: (c) => c.durationMs,
    fmt: (v) => fmtMs(v),
    why: "duration",
  },
  {
    key: "voice:p50",
    label: "Latency p50",
    get: (c) => c.p50,
    fmt: (v) => fmtLatency(v),
    why: "latency",
  },
  {
    key: "voice:p90",
    label: "Latency p90",
    get: (c) => c.p90,
    fmt: (v) => fmtLatency(v),
    why: "latency",
  },
  {
    key: "voice:kept",
    label: "Kept talking over the caller",
    get: (c) => c.kept,
    fmt: (v) => fmtCount(v),
    why: "interruptions",
  },
  {
    key: "voice:wer",
    label: "WER",
    get: (c) => c.wer,
    fmt: (v) => fmtPct(v),
    why: "speech recognition",
    optional: true,
  },
  {
    key: "voice:talk",
    label: "Talk ratio",
    get: (c) => c.talk,
    fmt: (v) => `${fmtPct(v, { dp: 0 })} agent`,
    why: "audio",
    optional: true,
  },
  {
    key: "voice:cost",
    label: "Cost per call",
    get: (c) => c.cost?.total ?? null,
    fmt: (v) => fmtCost(v),
    text: true,
    tip: (c) =>
      [
        ["STT", c.cost?.stt],
        ["LLM", c.cost?.llm],
        ["TTS", c.cost?.tts],
        ["Telephony", c.cost?.telephony],
      ]
        .filter(([, v]) => v != null)
        .map(([k, v]) => `${k} ${fmtCost(v)}`)
        .join(" · ") || null,
  },
]);
const VOICE_CELL_STYLE = {
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  fontSize: 13,
  paddingTop: 8,
};

// ------------------------------------------------------- trace roll-ups

/** New roll-up columns: hidden until turned on (opt-in keys). */
export const ROLLUP_KEYS = Object.freeze({
  steps: "rollup:steps",
  toolCalls: "rollup:tool_calls",
  toolErrors: "rollup:tool_errors",
  turns: "rollup:turns",
});
const OPT_IN = new Set(Object.values(ROLLUP_KEYS));
const ROLLUP_LABEL = {
  [ROLLUP_KEYS.steps]: "Steps",
  [ROLLUP_KEYS.toolCalls]: "Tool calls",
  [ROLLUP_KEYS.toolErrors]: "Tool errors",
  [ROLLUP_KEYS.turns]: "Turns",
};
const RENAME = { "ops:latency": "Wall time" };

/**
 * Opt-in keys are stored as `+<key>` in the hidden set (so a viewer's saved
 * column prefs from before R2 keep them off); every other key is shown
 * unless it is in the set.
 */
export const isOptInKey = (key) => OPT_IN.has(key);
export const columnToggleKey = (key) => (isOptInKey(key) ? `+${key}` : key);
export function isColumnShown(hidden, key) {
  const set = hidden ?? new Set();
  return isOptInKey(key) ? set.has(`+${key}`) : !set.has(key);
}

const finite = (v) => typeof v === "number" && Number.isFinite(v);
const meanOf = (xs) => {
  const nums = xs.filter(finite);
  return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null;
};

/**
 * One roll-up value of a result (mean over its trials), or null when the
 * result has no trace data for it. Pure.
 */
export function rollupValue(result, key) {
  if (!result || ["missing", "queued"].includes(result.status)) return null;
  const sums = (result.trace_summary ?? []).filter(
    (t) => t && !["none", "infra"].includes(t.status),
  );
  if (key === ROLLUP_KEYS.steps)
    return meanOf(sums.map((t) => t.steps)) ?? result.steps?.steps ?? null;
  if (key === ROLLUP_KEYS.toolCalls)
    return (
      meanOf(sums.map((t) => t.tool_calls)) ?? result.steps?.tool_calls ?? null
    );
  if (key === ROLLUP_KEYS.toolErrors) {
    if (finite(result.steps?.tool_errors)) return result.steps.tool_errors;
    if (!sums.length) return null;
    return meanOf(
      sums.map((t) =>
        t.status === "error" && /\b[45]\d\d\b/.test(t.error?.text ?? "")
          ? 1
          : 0,
      ),
    );
  }
  if (key === ROLLUP_KEYS.turns)
    return meanOf(sums.map((t) => (finite(t.turns) ? t.turns : null)));
  return null;
}

/** `5` · `5.3` (trials differ) · `—`. */
export function formatRollup(v) {
  if (!finite(v)) return "—";
  return fmtNumber(Math.round(v * 10) / 10);
}

/**
 * The `Trace roll-ups` entries of Columns…, in spec order. Steps maps to
 * the existing agent column when the view has one.
 */
export function rollupEntries(summary) {
  const system = new Set((summary?.system_metrics ?? []).map((d) => d.key));
  return [
    {
      key: system.has("agent:steps") ? "agent:steps" : ROLLUP_KEYS.steps,
      label: "Steps",
    },
    { key: ROLLUP_KEYS.toolCalls, label: "Tool calls" },
    { key: ROLLUP_KEYS.toolErrors, label: "Tool errors" },
    { key: ROLLUP_KEYS.turns, label: "Turns" },
    ...(system.has("ops:cost") ? [{ key: "ops:cost", label: "Cost" }] : []),
    ...(system.has("ops:latency")
      ? [{ key: "ops:latency", label: "Wall time" }]
      : []),
  ];
}

const ROLLUP_CELL_STYLE = {
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  fontSize: 13,
  paddingTop: 8,
};

/** Every per-run column group in view order, for Columns… and the grid. */
export function metricGroups(summary) {
  if (!summary) return [];
  const allRuns = (summary.runs ?? [])
    .filter((r) => !r.not_found)
    .map((r) => r.run_id);
  const groups = [];
  (summary.metrics ?? []).forEach((def) =>
    groups.push({
      key: def.key,
      def,
      kind: "eval",
      runIds: allRuns.filter((id) => (def.runs ?? allRuns).includes(id)),
    }),
  );
  (summary.human_metrics ?? []).forEach((def) =>
    groups.push({ key: def.key, def, kind: "human", runIds: allRuns }),
  );
  (summary.system_metrics ?? []).forEach((def) => {
    if (def.key.startsWith("agent:") && def.key !== "agent:steps") return;
    groups.push({ key: def.key, def, kind: "system", runIds: allRuns });
  });
  return groups;
}

/**
 * W1-3: the header note of a delta between two eval versions: refused,
 * with "Re-score #2A on v3" (the run on the older version, on the newer
 * one, when that is a version of the template) and "Compare anyway"; or
 * compared anyway ("mixed versions").
 */
export function versionNoteOf(
  entry,
  { runLabel, baseLabel, compareAnyway, rescoreOn, runId, baseId, latest },
) {
  const refused = notComparedNote(entry, { runLabel, baseLabel });
  if (refused) {
    const target = rescoreTargetOf(entry, { runId, baseId, latest });
    const actions = [
      ...(rescoreOn && target
        ? [
            {
              label: `Re-score ${
                (target.runId === runId ? runLabel : baseLabel) ?? "the run"
              } on ${target.version}`,
              onClick: () => rescoreOn(target.runId, target.version),
            },
          ]
        : []),
      ...(compareAnyway
        ? [{ label: "Compare anyway", onClick: compareAnyway }]
        : []),
    ];
    if (actions.length > 1)
      return { ...refused, tone: "muted", actionLabel: "Resolve", actions };
    return {
      ...refused,
      tone: "muted",
      ...(actions.length
        ? { actionLabel: actions[0].label, onAction: actions[0].onClick }
        : {}),
    };
  }
  const mixed = mixedNote(entry);
  return mixed ? { ...mixed, tone: "muted" } : undefined;
}

/**
 * W1-8: the Aggregate mode a run's header reads: `run` (the default) is the
 * run's own trial reducer (R3 `run_reducer`), else the Average.
 */
export function resolveAgg(entry, agg) {
  if (agg !== "run") return agg;
  const own = entry?.run_reducer;
  return own && entry?.reducers?.[own] ? own : "avg";
}

/** The rule a header aggregate reads: a reducer, `avg`, `median`, `pass_rate`. */
const ruleOfAggregate = (a) => (a ? a.reducer ?? a.agg ?? null : null);

/** `pass^3`, `Mean`, `Median`, `Pass rate`: a rule as a header names it. */
export function ruleLabelOf(rule, k = null) {
  if (!rule) return null;
  if (rule === "avg") return "Mean";
  if (rule === "pass_rate") return "Pass rate";
  if (rule === "na_rate") return "N/A rate";
  return reducerLabel(rule, k);
}

/** Header aggregate for the chosen Aggregate mode (`avg|median|pass|run|…`). */
export function aggregateFor(def, entry, aggMode) {
  if (!entry) return null;
  const agg = resolveAgg(entry, aggMode);
  // Cost per row, so the header delta (per-row mean difference) reads on
  // the same basis; the run total is in Run details and Summary.
  if (def?.key === "ops:cost") return { value: entry.avg, agg: "avg" };
  if (def?.key === "ops:latency")
    return agg === "avg"
      ? { value: entry.avg, agg: "avg" }
      : { value: entry.p50 ?? entry.median, agg: "p50" };
  // W1-4: the N/A rate of an eval (rows the judge said N/A on).
  if (agg === "na_rate" && entry.n_na != null)
    return { value: entry.na_rate ?? 0, agg: "na_rate" };
  if (agg === "median") return { value: entry.median, agg: "median" };
  // Review S4 (W1-8): Average and Pass rate carry their 95% interval too
  // (R3 `value_ci95` for the mean; Wilson over the run's scored rows for a
  // pass rate), shown under the value like a reducer's.
  if (agg === "pass" && entry.pass_rate != null) {
    const ci = rateInterval(entry.pass_rate, entry.n);
    return {
      value: entry.pass_rate,
      agg: "pass_rate",
      ...(ci ? { ci95: ci } : {}),
    };
  }
  // W1-8: a trial reducer (R3 `reducers`): its value, a share printed as a
  // rate, with its 95% interval.
  const red = entry.reducers?.[agg];
  if (red && red.value != null) {
    const share =
      ["pass_fail", "thumbs"].includes(def?.output_type) ||
      /^(mode|pass_at_k|pass_hat_k|at_least:\d+)$/.test(agg);
    return {
      value: red.value,
      agg: share ? "pass_rate" : "avg",
      ci95: red.ci95 ?? null,
      reducer: agg,
    };
  }
  return {
    value: entry.avg,
    agg: "avg",
    ...(Array.isArray(entry.value_ci95) && entry.n > 1
      ? { ci95: entry.value_ci95 }
      : {}),
  };
}

/**
 * IT-R4 G3 (P1, rule 28): the header delta on the rows both runs scored,
 * in the statistic the header shows (candidate on shared − baseline on
 * shared). Null without the wire's `shared` block.
 */
export function sharedDeltaOf(def, entry, agg) {
  const sh = entry?.shared;
  if (!sh?.n) return null;
  const c = aggregateFor(def, sh.cand, agg)?.value;
  const b = aggregateFor(def, sh.base, agg)?.value;
  return Number.isFinite(c) && Number.isFinite(b) ? c - b : null;
}

/**
 * IT-R4 G3 (P1): `on 9 shared` + `On the 9 rows both ran: #7B 55.6%, #1A
 * 55.6%. #7B has 10 rows so far.` when the shared rows are not both runs'
 * own rows (a partial run, a version that added rows). Null otherwise.
 */
export function basisOf(def, entry, agg, { candLabel, baseLabel } = {}) {
  const sh = entry?.shared;
  if (!sh?.n || (sh.n === sh.own_n && sh.n === sh.base_n)) return null;
  const c = aggregateFor(def, sh.cand, agg);
  const b = aggregateFor(def, sh.base, agg);
  const fmt = (a) => formatValue(def, a?.value, { agg: a?.agg });
  const cand = candLabel ?? "This run";
  const rows = entry.partial ? entry.done ?? sh.own_n : sh.own_n;
  return {
    label: `on ${fmtNumber(sh.n)} shared`,
    tooltip: [
      `On the ${fmtCount(sh.n, "row")} both ran: ${cand} ${fmt(c)}, ${baseLabel ?? "the baseline"} ${fmt(b)}.`,
      `${cand} has ${fmtCount(rows, "row")}${entry.partial ? " so far" : ""}.`,
    ].join(" "),
  };
}

/** The active movement direction for a metric + run, from the pills. */
export function activeMovement(pills, metric, runId) {
  const pill = (pills ?? []).find(
    (p) =>
      p.type === "movement" &&
      p.metric === metric &&
      (p.run ?? ANY_CANDIDATE) === runId,
  );
  return pill?.dir === "improved" || pill?.dir === "regressed"
    ? pill.dir
    : null;
}

const WIDTH = {
  row: 72,
  splits: 128,
  input: 280,
  expected: 280,
  output: 300,
  metric: 224,
  metricReasons: 280,
  ops: 204,
  steps: 204,
  pref: 180,
  meta: 130,
};
// Two or more runs: narrower text columns so two run outputs sit side by
// side on a 1280 px laptop (R4 integrate; users widen columns as before).
// IT-R5 G3 (P2/P3): the Row cell carries `Tone ↓` and the vote mark.
const MULTI_TEXT_WIDTH = { row: 120, input: 200, expected: 200, output: 260 };

/**
 * @param {object} o
 * @param {object} o.summary  R3 summary (page 0)
 * @param {string} o.baselineId
 * @param {object} o.runsById canonical runs by id
 * @param {object} o.colors   useRunColors map
 * @param {Set<string>} o.hidden  hidden column keys
 * @param {boolean} o.showReasons
 * @param {string} o.agg      avg | median | pass
 * @param {object[]} o.pills
 * @param {object} o.handlers { onMovement(metric, run, dir), groupMenu(group) -> items }
 * @param {boolean} [o.why]  the `Why` column (multi-run, Show ≠ All)
 * @param {object[]} [o.layout]  useColumnLayout().columns (resolved, in
 *   order): when given it decides order, hidden, frozen and user widths per
 *   catalog id (resultsColumnCatalog) and `hidden` is ignored
 * @returns {object[]} AG Grid column defs
 */
export function buildResultColumns({
  summary,
  baselineId,
  runsById = {},
  colors = {},
  hidden = new Set(DEFAULT_HIDDEN),
  showReasons = false,
  agg = "avg",
  pills = [],
  handlers = {},
  why = false,
  taskSuite = false,
  layout = null,
  voice = null,
}) {
  if (!summary) return [];
  const runIds = (summary.runs ?? [])
    .filter((r) => !r.not_found)
    .map((r) => r.run_id);
  const single = runIds.length < 2;
  const W = single ? WIDTH : { ...WIDTH, ...MULTI_TEXT_WIDTH };
  const baseLabel = runsById[baselineId]?.label;
  // With a layout every entry is built and the layout picks; else the
  // legacy hidden set does.
  const isHidden = (key) => (layout ? false : hidden.has(key));
  const optInShown = (key) => (layout ? true : isColumnShown(hidden, key));
  const entries = [];
  let current = null;
  const cols = {
    push: (def) => entries.push({ key: current, def }),
  };
  const at = (key) => {
    current = key;
    return cols;
  };

  at("row").push({
    colId: "row",
    headerName: taskSuite ? "Task" : "Row",
    field: "row_order",
    width: W.row,
    minWidth: 64,
    pinned: "left",
    lockPinned: true,
    cellRenderer: "rowCell",
    suppressMovable: true,
  });
  at("input").push({
    colId: "input",
    headerName: "Input",
    width: W.input,
    pinned: "left",
    cellRenderer: "inputCell",
    suppressMovable: true,
    suppressHeaderMenuButton: false,
  });
  if (!isHidden("splits"))
    at("splits").push({
      colId: "splits",
      headerName: "Splits",
      width: WIDTH.splits,
      cellRenderer: "splitsCell",
      suppressHeaderMenuButton: false,
    });
  // C5: no reference column → no Expected column.
  if (!isHidden("expected") && !summary.no_reference)
    at("expected").push({
      colId: "expected",
      width: W.expected,
      suppressHeaderMenuButton: false,
      headerName: summary.runs?.[0]?.dataset_version?.number
        ? `Expected · v${summary.runs[0].dataset_version.number}`
        : "Expected",
      cellRenderer: "expectedCell",
    });
  // iterate r2 G3 (P1): a re-test of production failures shows how each
  // row did in production, next to the new result.
  if (summary.production && !isHidden("production"))
    at("production").push({
      colId: "production",
      headerName: "Production reply",
      headerTooltip:
        "How this row did in production (the session it came from): the criterion it failed, at which turn, and what production replied. Hover a cell for the judge's reason.",
      width: 220,
      cellRenderer: "productionCell",
      suppressHeaderMenuButton: false,
    });
  // R3 RC#1: why each row is in Regressed / Improved / Changed. A3-05:
  // data-driven (multi-run, Show ≠ All), hideable like any column.
  if (why && !single)
    at("why").push({
      colId: "why",
      headerName: "Why",
      headerTooltip:
        "The evals that moved on this row vs the baseline, worst first. Click a chip to go to its column.",
      width: 260,
      cellRenderer: "whyCell",
      suppressHeaderMenuButton: false,
    });
  if (!isHidden("meta"))
    (summary.metadata_keys ?? []).forEach((key) =>
      at("meta").push({
        colId: `meta:${key}`,
        headerName: key,
        width: WIDTH.meta,
        cellRenderer: "metaCell",
        cellRendererParams: { metaKey: key },
        suppressHeaderMenuButton: false,
      }),
    );

  // HO R1 (A-H1): one group per custom column (values from R3 `custom`),
  // left of Output so a new one is in view.
  pushCustomColumns((key, def) => at(key).push(def), {
    summary,
    runIds,
    runsById,
    colors,
    baselineId,
    single,
    groupMenu: handlers.groupMenu,
    isHidden,
  });
  // Output per run.
  if (!isHidden(OUTPUT_KEY))
    at(OUTPUT_KEY).push({
      groupId: OUTPUT_KEY,
      marryChildren: true,
      headerName: "Output",
      headerGroupComponent: "resultGroupHeader",
      headerGroupComponentParams: {
        resultGroup: { key: OUTPUT_KEY, kind: "output", name: "Output" },
      },
      children: runIds.map((runId) => ({
        colId: colIdFor(OUTPUT_KEY, runId),
        headerName: runsById[runId]?.label ?? "Output",
        width: W.output,
        headerComponent: "resultRunHeader",
        headerComponentParams: {
          run: runsById[runId],
          color: colors[runId],
          isBaseline: runId === baselineId && !single,
        },
        cellRenderer: "outputCell",
        cellRendererParams: { runId },
      })),
    });
  const groups = metricGroups(summary);
  const pushGroup = (group) => {
    if (isHidden(group.key)) return;
    const def = group.def;
    const isSystem = group.kind === "system";
    const width =
      group.key === "agent:steps"
        ? WIDTH.steps
        : isSystem
          ? WIDTH.ops
          : showReasons
            ? WIDTH.metricReasons
            : WIDTH.metric;
    const groupName = RENAME[group.key] ?? def.name;
    // Review S5 (W1-8): when the runs of this group read different rules
    // (one run set pass^3, the other is read by its mean), each header
    // names its rule before the value.
    const rules = group.runIds.map((id) =>
      ruleOfAggregate(
        aggregateFor(def, summary.aggregates?.[group.key]?.[id], agg),
      ),
    );
    const mixedRules = new Set(rules.filter(Boolean)).size > 1;
    at(group.key).push({
      groupId: group.key,
      marryChildren: true,
      ...(!isSystem && group.key !== "agent:steps"
        ? {
            // IT-R4 G3 (P1): room for `on 9 shared` beside the delta.
            compactWidth: group.runIds.some((id) =>
              basisOf(def, summary.aggregates?.[group.key]?.[id], agg),
            )
              ? PINNED_SCORE_WIDTH + BASIS_EXTRA
              : PINNED_SCORE_WIDTH,
          }
        : {}),
      headerName: groupName,
      headerGroupComponent: "resultGroupHeader",
      headerGroupComponentParams: {
        resultGroup: {
          key: group.key,
          kind: group.kind,
          name: groupName,
          def,
          versions: def.versions,
          versionsDiffer: !!def.versions_differ,
          // W1-3: the template's newest version, and See changes.
          latestVersion: def.latest_version ?? null,
          onSeeChanges: handlers.seeChanges
            ? () => handlers.seeChanges(group)
            : undefined,
          mixedVersions: group.runIds.some(
            (id) => summary.aggregates?.[group.key]?.[id]?.mixed_versions,
          ),
          scoredWith: def.scored_with,
          runLabels: Object.fromEntries(
            Object.keys(def.versions ?? {}).map((id) => [
              id,
              runsById[id]?.label,
            ]),
          ),
          menuItems: handlers.groupMenu?.(group) ?? [],
        },
      },
      children: group.runIds.map((runId) => {
        const entry = summary.aggregates?.[group.key]?.[runId];
        const isBaseline = runId === baselineId && !single;
        return {
          colId: colIdFor(group.key, runId),
          headerName: `${groupName} ${runsById[runId]?.label ?? ""}`.trim(),
          width,
          headerComponent: "resultMetricHeader",
          headerComponentParams: {
            def,
            run: runsById[runId],
            color: colors[runId],
            isBaseline,
            aggregate: aggregateFor(def, entry, agg),
            ruleLabel: mixedRules
              ? ruleLabelOf(
                  ruleOfAggregate(aggregateFor(def, entry, agg)),
                  def.trials_k,
                )
              : undefined,
            // W1-4: rows left out of the value (N/A, score errors).
            counts: entry?.n_na || entry?.n_err ? entry : undefined,
            // W1-3: a delta between two eval versions is refused until
            // the user compares anyway (then the header says so).
            versionNote: versionNoteOf(entry, {
              runLabel: runsById[runId]?.label,
              baseLabel,
              compareAnyway: handlers.compareAnyway,
              rescoreOn: handlers.rescoreOn
                ? (id, version) => handlers.rescoreOn(group, id, version)
                : undefined,
              runId,
              baseId: baselineId,
              latest: def.latest_version,
            }),
            partial: entry?.partial
              ? { done: entry.done, total: entry.total }
              : false,
            delta:
              !isBaseline &&
              !single &&
              agg !== "na_rate" &&
              !entry?.reducers?.[resolveAgg(entry, agg)] &&
              Number.isFinite(entry?.delta)
                ? {
                    delta: sharedDeltaOf(def, entry, agg) ?? entry.delta,
                    ci95: entry.ci95,
                    significant: entry.significant,
                    n: entry.n_shared,
                    vsLabel: baseLabel,
                  }
                : undefined,
            basis:
              !isBaseline &&
              !single &&
              !entry?.reducers?.[resolveAgg(entry, agg)]
                ? basisOf(def, entry, agg, {
                    candLabel: runsById[runId]?.label,
                    baseLabel,
                  })
                : undefined,
            movement:
              !isBaseline && !single && entry && entry.improved != null
                ? {
                    improved: entry.improved,
                    regressed: entry.regressed,
                    unchanged: entry.unchanged,
                    not_comparable: entry.not_comparable,
                    active: activeMovement(pills, group.key, runId),
                  }
                : undefined,
            onMovement: handlers.onMovement
              ? (dir) => handlers.onMovement(group.key, runId, dir)
              : undefined,
          },
          cellRenderer: isSystem
            ? group.key === "agent:steps"
              ? "stepsCell"
              : "opsCell"
            : "scoreCell",
          cellRendererParams: { runId, metricKey: group.key },
          cellStyle: isSystem ? undefined : { padding: 0 },
        };
      }),
    });
  };

  groups.filter((g) => g.kind === "eval").forEach(pushGroup);
  groups.filter((g) => g.kind === "human").forEach(pushGroup);

  const prefRuns = Object.keys(summary.preference ?? {});
  if (!single && prefRuns.length && !isHidden(PREFERENCE_KEY)) {
    const candidate = prefRuns[0];
    const pref = summary.preference[candidate];
    const hasJudge =
      (pref.judge?.wins ?? 0) +
        (pref.judge?.ties ?? 0) +
        (pref.judge?.losses ?? 0) >
      0;
    at(PREFERENCE_KEY).push({
      groupId: PREFERENCE_KEY,
      marryChildren: true,
      headerName: "Preference",
      headerGroupComponent: "resultGroupHeader",
      headerGroupComponentParams: {
        resultGroup: {
          key: PREFERENCE_KEY,
          kind: "preference",
          name: `Preference · ${runsById[candidate]?.label ?? ""} vs ${baseLabel ?? ""}`,
          menuItems:
            handlers.groupMenu?.({ key: PREFERENCE_KEY, kind: "preference" }) ??
            [],
        },
      },
      children: [
        {
          colId: "preference|human",
          headerName: "Human",
          width: WIDTH.pref,
          headerComponent: "resultPreferenceHeader",
          headerComponentParams: { source: "human", summary: pref, candidate },
          cellRenderer: "preferenceCell",
          cellRendererParams: { source: "human", candidate },
        },
        ...(hasJudge
          ? [
              {
                colId: "preference|judge",
                headerName: "LLM judge",
                width: WIDTH.pref,
                headerComponent: "resultPreferenceHeader",
                headerComponentParams: {
                  source: "judge",
                  summary: pref,
                  candidate,
                },
                cellRenderer: "preferenceCell",
                cellRendererParams: { source: "judge", candidate },
              },
            ]
          : []),
      ],
    });
  }

  groups.filter((g) => g.kind === "system").forEach(pushGroup);

  // Voice columns (scenarios datasets with calls).
  if (voice?.calls)
    VOICE_COLUMNS.forEach((vc) => {
      if (isHidden(vc.key)) return;
      at(vc.key).push({
        groupId: vc.key,
        marryChildren: true,
        headerName: vc.label,
        headerGroupComponent: "resultGroupHeader",
        headerGroupComponentParams: {
          resultGroup: { key: vc.key, kind: "system", name: vc.label },
        },
        children: runIds.map((runId) => ({
          colId: colIdFor(vc.key, runId),
          headerName: runsById[runId]?.label ?? vc.label,
          width: vc.key === "voice:kept" ? 132 : 112,
          headerComponent: "resultRunHeader",
          headerComponentParams: {
            run: runsById[runId],
            color: colors[runId],
            isBaseline: runId === baselineId && !single,
          },
          valueGetter: (p) => {
            const c =
              voice.calls[p.data?.example_id ?? p.data?.row_id]?.[runId];
            if (!c || (c.textOnly && !vc.text)) return null;
            return vc.get(c);
          },
          valueFormatter: (p) => (p.value == null ? EMPTY : vc.fmt(p.value)),
          tooltipValueGetter: (p) => {
            const c =
              voice.calls[p.data?.example_id ?? p.data?.row_id]?.[runId];
            if (c?.textOnly && !vc.text) return `${TEXT_ONLY_WHY} ${vc.why}`;
            return c && vc.tip ? vc.tip(c) : null;
          },
          cellStyle: VOICE_CELL_STYLE,
          sortable: false,
        })),
      });
    });

  // Trace roll-ups (opt-in): one right-aligned number per run.
  const hasAgentSteps = groups.some((g) => g.key === "agent:steps");
  Object.values(ROLLUP_KEYS).forEach((key) => {
    if (key === ROLLUP_KEYS.steps && hasAgentSteps) return;
    if (!optInShown(key)) return;
    at(key).push({
      groupId: key,
      marryChildren: true,
      headerName: ROLLUP_LABEL[key],
      headerGroupComponent: "resultGroupHeader",
      headerGroupComponentParams: {
        resultGroup: { key, kind: "system", name: ROLLUP_LABEL[key] },
      },
      children: runIds.map((runId) => ({
        colId: colIdFor(key, runId),
        headerName: runsById[runId]?.label ?? ROLLUP_LABEL[key],
        width: 112,
        headerComponent: "resultRunHeader",
        headerComponentParams: {
          run: runsById[runId],
          color: colors[runId],
          isBaseline: runId === baselineId && !single,
        },
        valueGetter: (p) => rollupValue(p.data?.results?.[runId], key),
        valueFormatter: (p) => formatRollup(p.value),
        cellStyle: ROLLUP_CELL_STYLE,
        sortable: false,
      })),
    });
  });
  return layout ? shapeColumns(entries, layout) : entries.map((e) => e.def);
}

/** The catalog id of a grid column id (`ops:cost|<run>` → `ops:cost`). */
export const keyOfColId = (colId) => {
  const k = String(colId ?? "").split("|")[0];
  return k.startsWith("meta:") ? "meta" : k;
};

const withShape = (def, col) => {
  const pinned = col?.pinned === "left" ? "left" : null;
  // P8: a frozen score group is compact unless the viewer sized it.
  const compact = pinned && def.compactWidth && !col?.width;
  const leaf = (d) => ({
    ...d,
    pinned,
    ...(col?.width
      ? { width: col.width }
      : compact
        ? {
            width: def.compactWidth,
            // The ↑ ↓ movement filters live on the Show chips; the compact
            // header keeps the value and the delta readable.
            ...(d.headerComponentParams?.movement
              ? {
                  headerComponentParams: {
                    ...d.headerComponentParams,
                    movement: undefined,
                  },
                }
              : {}),
          }
        : {}),
  });
  return def.children
    ? { ...def, children: def.children.map(leaf) }
    : leaf(def);
};

/**
 * Order, hide, freeze and size built entries by the resolved layout. Pure.
 * Entries the catalog does not know keep their place at the end.
 */
export function shapeColumns(entries, layout) {
  const byId = new Map((layout ?? []).map((c) => [c.id, c]));
  return entries
    .map((e, i) => ({ ...e, i, col: byId.get(e.key) }))
    .filter((e) => !e.col?.hidden)
    .sort(
      (a, b) =>
        (a.col ? a.col.index : 1e6 + a.i) - (b.col ? b.col.index : 1e6 + b.i),
    )
    .map((e) => (e.col ? withShape(e.def, e.col) : e.def));
}

/**
 * The results grid's column catalog for useColumnLayout (ONE-VIEW-SPEC
 * §3.2): one entry per column the user shapes (a per-run group is one
 * entry: hiding `Cost` hides it for every run). Row and Input are locked
 * and frozen; Splits, Metadata, Tokens and the new trace roll-ups start
 * hidden. Pure.
 * @param {object} summary  R3 summary
 * @param {{why?: boolean, taskSuite?: boolean}} [o]
 */
/** P8: the width of a frozen score column (value + run chip). */
export const PINNED_SCORE_WIDTH = 172;
/** IT-R4 G3 (P1): extra width when a header prints its basis. */
const BASIS_EXTRA = 32;
const PINNED_CENTER_MIN = 360;
/** P9: the frozen columns never take more than this share of the grid. */
export const FROZEN_MAX_SHARE = 0.4;

/**
 * P8: which score groups start frozen after Input — every eval when they
 * fit beside a readable centre (≥ 360 px), else the primary one, else none
 * (frozen columns that do not fit make AG Grid unfreeze Input). Pure.
 * @returns {"all"|"primary"|null}
 */
export function scorePinFor(summary, { gridWidth, primaryKey } = {}) {
  if (!summary || !Number.isFinite(gridWidth)) return null;
  const runs = Math.max(
    1,
    (summary.runs ?? []).filter((r) => !r.not_found).length,
  );
  const evals = metricGroups(summary).filter(
    (g) => g.kind === "eval" || g.kind === "human",
  );
  if (!evals.length) return null;
  const multi = runs > 1;
  const fixed =
    44 +
    (multi ? MULTI_TEXT_WIDTH.row : WIDTH.row) +
    (multi ? MULTI_TEXT_WIDTH.input : WIDTH.input);
  const room = (cells) =>
    gridWidth - fixed - cells * PINNED_SCORE_WIDTH >= PINNED_CENTER_MIN;
  // iterate r2 G3 (P9): freezing every eval stops at 40 % of the grid (row,
  // input and the scores), so the centre keeps the run columns; the primary
  // score alone stays frozen while the centre stays readable.
  if (
    room(evals.length * runs) &&
    fixed + evals.length * runs * PINNED_SCORE_WIDTH <=
      gridWidth * FROZEN_MAX_SHARE
  )
    return "all";
  if (primaryKey && evals.some((g) => g.key === primaryKey) && room(runs))
    return "primary";
  return null;
}

export function resultsColumnCatalog(
  summary,
  {
    why = false,
    whyDefault = true,
    taskSuite = false,
    voice = false,
    scorePin = null,
    primaryKey = null,
    expectedDefault = true,
  } = {},
) {
  if (!summary) return [];
  const runIds = (summary.runs ?? []).filter((r) => !r.not_found);
  const single = runIds.length < 2;
  // iterate r1 G3 (P8): the score columns (Task success + evals) start
  // frozen after Input when they have room (scorePinFor), so the verdict columns
  // never scroll off; the viewer can unfreeze / move them as any column.
  const pinScore = (key) =>
    !voice &&
    (scorePin === "all" || (scorePin === "primary" && key === primaryKey));
  const example = [
    {
      id: "row",
      label: taskSuite ? "Task" : "Row",
      group: "example",
      locked: true,
      defaultPinned: "left",
    },
    {
      id: "input",
      label: "Input",
      group: "example",
      locked: true,
      defaultPinned: "left",
    },
    { id: "splits", label: "Splits", group: "example", defaultVisible: false },
    // IT-R5 G3 (P1): below 1500 px Expected starts hidden when it is empty
    // or 3+ runs share the width (one click in Columns brings it back).
    {
      id: "expected",
      label: "Expected",
      group: "example",
      ...(expectedDefault ? {} : { defaultVisible: false }),
    },
    ...(summary.production
      ? [
          {
            id: "production",
            label: "Production reply",
            group: "example",
            description: "How each row did in production",
          },
        ]
      : []),
    ...(why && !single
      ? [
          {
            id: "why",
            label: "Why",
            group: "example",
            description: "The evals that moved on each row vs the baseline",
            // P8: shown by default only with the Regressed filter (the
            // viewer can still show or hide it).
            defaultVisible: whyDefault,
          },
        ]
      : []),
    ...((summary.metadata_keys ?? []).length
      ? [
          {
            id: "meta",
            label: "Metadata",
            group: "example",
            defaultVisible: false,
          },
        ]
      : []),
  ];
  const groups = metricGroups(summary);
  const perRun = [
    ...customCatalogEntries(summary),
    { id: OUTPUT_KEY, label: "Output", group: "perRun" },
    // Voice data: the call columns are the default; the eval columns stay
    // one click away under Columns.
    ...(voice
      ? VOICE_COLUMNS.map((vc) => ({
          id: vc.key,
          label: vc.label,
          group: "perRun",
          defaultVisible: !vc.optional,
        }))
      : []),
    ...groups
      .filter((g) => g.kind === "eval" || g.kind === "human")
      .map((g) => ({
        id: g.key,
        label: g.def.name,
        group: "perRun",
        defaultVisible: !voice,
        ...(pinScore(g.key) ? { defaultPinned: "left" } : {}),
      })),
    ...(!single && Object.keys(summary.preference ?? {}).length
      ? [{ id: PREFERENCE_KEY, label: "Preference", group: "perRun" }]
      : []),
    ...groups
      .filter((g) => g.kind === "system")
      .map((g) => ({
        id: g.key,
        label: RENAME[g.key] ?? g.def.name,
        group: "perRun",
        // Voice data: Duration and Cost per call replace Wall time / Cost.
        defaultVisible: !voice && !DEFAULT_HIDDEN.includes(g.key),
      })),
  ];
  const hasAgentSteps = groups.some((g) => g.key === "agent:steps");
  const rollups = Object.values(ROLLUP_KEYS)
    .filter((key) => !(key === ROLLUP_KEYS.steps && hasAgentSteps))
    .map((key) => ({
      id: key,
      label: ROLLUP_LABEL[key],
      group: "rollups",
      defaultVisible: false,
    }));
  return [...example, ...perRun, ...rollups];
}

/** Column groups of the Columns panel. */
export const RESULTS_COLUMN_GROUPS = Object.freeze([
  { id: "example", label: "Row fields" },
  { id: "perRun", label: "Per run" },
  { id: CUSTOM_GROUP, label: "Custom columns" },
  { id: "rollups", label: "Trace roll-ups (per attempt, averaged)" },
]);

/**
 * One-time migration of the old per-dataset hidden set
 * (`results.cols.<datasetId>`, `+key` = opt-in shown) into column prefs
 * (`{visible}` overrides only). Pure.
 */
export function legacyHiddenToPrefs(hiddenList, catalog) {
  const hidden = new Set(hiddenList ?? []);
  const visible = {};
  (catalog ?? []).forEach((c) => {
    if (c.locked) return;
    const key = c.id;
    const shown = isColumnShown(hidden, key);
    const def = c.defaultVisible !== false;
    if (shown !== def) visible[key] = shown;
  });
  return Object.keys(visible).length ? { visible } : {};
}

/** Columns… menu entries: Example columns, then per-run groups. */
export function columnMenuEntries(summary) {
  const per = metricGroups(summary).map((g) => ({
    key: g.key,
    label: g.def.name,
  }));
  return {
    example: EXAMPLE_COLUMNS,
    perRun: [
      { key: OUTPUT_KEY, label: "Output" },
      ...per.filter(
        (g) => !g.key.startsWith("ops:") && !g.key.startsWith("agent:"),
      ),
      ...(Object.keys(summary?.preference ?? {}).length
        ? [{ key: PREFERENCE_KEY, label: "Preference" }]
        : []),
      ...per.filter((g) => g.key === "ops:tokens"),
    ],
    rollups: rollupEntries(summary),
  };
}
