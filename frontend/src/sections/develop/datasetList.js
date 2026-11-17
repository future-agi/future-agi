// Dataset list: pure helpers (R3 P1). No React, no I/O; tested in
// __tests__/datasetList.test.js.
import {
  fmtCount,
  fmtPct,
  fmtTimeRelative,
} from "src/sections/common/format/fmt";
import { VOCAB } from "src/sections/common/vocab";
import { parseRunLabel } from "src/sections/common/Runs/runLabel";
import {
  REAL_DEFAULT_SORT,
  realListColumnIds,
  REAL_LIST_DEFAULT_VISIBLE,
  datasetCaps,
} from "./realBackendCaps";

/** F3: a task suite's headline (pass@1) is always called Success rate. */
export const metricLabel = (base) =>
  base?.metric_key === "eval:task_success"
    ? VOCAB.SUCCESS_RATE
    : base?.metric_name ?? null;

/** URL view state of the list (`useViewUrlState`, replace): DL#13. */
// L1: the real backend (get-datasets) has no updated_at; it opens newest
// created first.
export const LIST_URL_DEFAULTS = Object.freeze({
  q: "",
  sort: datasetCaps().real ? REAL_DEFAULT_SORT : "updated_at:desc",
  page: 0,
  size: 25,
  // the interface contract, A3: the structured filter (compact JSON) and Group by.
  f: "",
  group: "",
});

export const PAGE_SIZES = Object.freeze([10, 25, 50, 100]);

/**
 * Column id (table) ⇄ API sort field. `Baseline` sorts by the API's
 * `score_now` (the pinned baseline's headline): Score now lives on in it.
 */
export const SORT_FIELD_BY_COLUMN = Object.freeze({
  name: "name",
  numberOfDatapoints: "number_of_datapoints",
  numberOfExperiments: "number_of_experiments",
  lastExperiment: "last_experiment_at",
  baseline: "score_now",
  bestRun: "best_delta",
  createdAt: "created_at",
  updatedAt: "updated_at",
});
const COLUMN_BY_SORT_FIELD = Object.fromEntries(
  Object.entries(SORT_FIELD_BY_COLUMN).map(([c, f]) => [f, c]),
);

/** `updated_at:desc` → `[{id: "updatedAt", desc: true}]` (unknown → []). */
export function sortingFromParam(param) {
  const [field, dir] = String(param ?? "").split(":");
  const id = COLUMN_BY_SORT_FIELD[field];
  return id ? [{ id, desc: dir !== "asc" }] : [];
}

/** Inverse of `sortingFromParam`; no sort → `none`. */
export function sortParamFrom(sorting) {
  const s = sorting?.[0];
  const field = s ? SORT_FIELD_BY_COLUMN[s.id] : null;
  return field ? `${field}:${s.desc ? "desc" : "asc"}` : "none";
}

/**
 * Split `text` around case-insensitive matches of `query` (DL#14/15).
 * @returns {{text: string, match: boolean}[]}
 */
export function highlightParts(text, query) {
  const s = String(text ?? "");
  const q = String(query ?? "").trim();
  if (!q || !s) return [{ text: s, match: false }];
  const lower = s.toLowerCase();
  const needle = q.toLowerCase();
  const out = [];
  let i = 0;
  while (i < s.length) {
    const at = lower.indexOf(needle, i);
    if (at < 0) {
      out.push({ text: s.slice(i), match: false });
      break;
    }
    if (at > i) out.push({ text: s.slice(i, at), match: false });
    out.push({ text: s.slice(at, at + needle.length), match: true });
    i = at + needle.length;
  }
  return out;
}

/**
 * Where the dataset stands today (SVP-4.1) = the pinned baseline's headline
 * (`Success rate · #2B`); without a pinned baseline, the best completed run.
 * Feeds the Baseline cell's tooltip (A1-01: Score now folded into Baseline).
 */
export function scoreNow(row) {
  const best = row?.best_run;
  const base = row?.baseline;
  const metric = metricLabel(base);
  if (base?.display && base.display !== "—")
    return { display: base.display, run: base.label, metric, pinned: true };
  if (best?.display && best.display !== "—")
    return { display: best.display, run: best.label, metric, pinned: false };
  return null;
}

/**
 * The plain word for the run behind the score (`baseline` / `best run`),
 * the glossary entry that explains it, and the run id spelled out for its
 * popover: `#2B is run B of experiment 2.` (DL#22, critic r3).
 */
export function scoreRunPhrase(s) {
  if (!s) return null;
  const word = s.pinned ? "baseline" : "best run";
  const term = s.pinned ? "baseline" : "run";
  const parsed = parseRunLabel(s.run);
  const id = parsed
    ? `${s.run} is run ${parsed.letter} of experiment ${parsed.ordinal}`
    : s.run || "";
  const why = s.pinned
    ? "the run new experiments are compared to"
    : "the best score so far";
  return {
    word,
    term,
    extra: id ? `Here: ${id}, ${why}.` : `Here: ${why}.`,
  };
}

/** `Best run #2B: 86.6% Success rate (+7.1 pts vs baseline #1A, 79.4%)` (DL#22). */
export function bestRunTooltip(row, deltaText) {
  const best = row?.best_run;
  if (!best) return "No completed run with 30+ rows yet";
  const base = row.baseline;
  const metric = metricLabel(base) ? ` ${metricLabel(base)}` : "";
  const head = `Best run ${best.label}: ${best.display}${metric}`;
  if (!base || base.run_id === best.run_id)
    return `${head}. It is also the pinned baseline.`;
  const vs = `vs baseline ${base.label}, ${base.display}`;
  // IT-R4 G2 P2: the population, as the Experiments tab counts it.
  if (best.basis) return `${head} ${vs}. ${best.basis}`;
  const noise = best.significant
    ? ""
    : ` · within noise (n=${best.n_shared ?? "?"})`;
  return `${head} (${deltaText ? `${deltaText} ` : ""}${vs})${noise}`;
}

/** `Run #7 started 2h ago · 52 of 248 rows done (21%)` (DL#21). */
export function runningTooltip(exp, rows, { now } = {}) {
  if (!exp) return "";
  const pct = Math.max(0, Math.min(1, exp.progress ?? 0));
  const total = Number(rows) || 0;
  const done = Math.round(pct * total);
  const started = exp.created_at
    ? ` started ${fmtTimeRelative(exp.created_at, { now })}`
    : "";
  const progress = total
    ? ` · ${fmtCount(done)} of ${fmtCount(total, "row")} done (${fmtPct(pct, { dp: 0 })})`
    : ` · ${fmtPct(pct, { dp: 0 })} done`;
  return `Experiment #${exp.ordinal}${started}${progress}. Click to open it.`;
}

/**
 * Delete confirm copy (DL#16, DL#45): title names the object, body states
 * what else goes, typed confirm only when experiments use the dataset.
 */
export function deleteConfirmCopy(items) {
  const list = items ?? [];
  const experiments = list.reduce(
    (n, d) => n + (Number(d.numberOfExperiments) || 0),
    0,
  );
  const versions = list.reduce(
    (n, d) => n + (Number(d.latest_version?.number) || 0),
    0,
  );
  const title =
    list.length === 1
      ? `Delete "${list[0].name}"?`
      : `Delete ${fmtCount(list.length, "dataset")}?`;
  const also = [
    experiments ? fmtCount(experiments, "experiment") : null,
    versions ? fmtCount(versions, "version") : null,
  ].filter(Boolean);
  const body = `${also.length ? `This also removes ${also.join(" and ")}. ` : ""}You can undo for 10 seconds.`;
  const typedConfirm =
    list.length === 1 && experiments > 0 ? list[0].name : undefined;
  return {
    title,
    body,
    typedConfirm,
    confirmLabel: list.length > 1 ? `Delete ${list.length}` : "Delete",
    names: list.map((d) => d.name),
  };
}

/** Layout surface of the list (ONE-VIEW-SPEC §3.2, A1-01). */
export const DATASET_LIST_SURFACE = "datasetList";

/** Header HoverCard of `Baseline` (A1-01): Score now is folded into it. */
export const BASELINE_HELP =
  "The pinned baseline's score on this dataset. The metric is named under it.";

/**
 * The list's column catalog (A1-01), in catalog order. The default set
 * (Name · Rows · Last experiment · Baseline · Best · Updated · ⋯) answers
 * "where does each dataset stand" and fits 1280 px; the rest is one click
 * away behind the `6 hidden` chip. `Score now` is retired: its metric name
 * is the second line of `Baseline`. The `⋯` column is not in the catalog
 * (always last, never hidden).
 */
export const DATASET_LIST_COLUMNS = Object.freeze([
  { id: "name", label: "Name", locked: true, defaultPinned: "left" },
  { id: "numberOfDatapoints", label: "Rows" },
  { id: "version", label: "Version", defaultVisible: false },
  { id: "lastExperiment", label: "Last experiment" },
  { id: "baseline", label: "Baseline", description: BASELINE_HELP },
  { id: "bestRun", label: "Best" },
  { id: "numberOfExperiments", label: "Experiments", defaultVisible: false },
  { id: "updatedAt", label: "Updated" },
  { id: "createdAt", label: "Created", defaultVisible: false },
  { id: "owner", label: "Owner", defaultVisible: false },
  {
    id: "numberOfOptimisations",
    label: "Optimizations",
    defaultVisible: false,
  },
  // v2.1 g8 (list:view): no Derived column; its count opened nothing.
  { id: "source", label: "Source", defaultVisible: false },
]);

/**
 * Line 2 of the Baseline cell: the metric the baseline's score is in
 * (`Success rate`), or null when no baseline is pinned.
 */
export function baselineMetric(row) {
  return row?.baseline ? metricLabel(row.baseline) : null;
}

// IT-R1 G2 P7: what a dataset tests, in one line under its name, from its
// kind and counts (never its free-text description, which stays in the
// tooltip): `Task suite · 30 tasks with end-state checks`,
// `Voice · 24 simulated callers`, `Multimodal · chat, image, audio, docs`.
const MEDIA_WORDS = [
  { word: "image", types: ["image", "images"] },
  { word: "audio", types: ["audio"] },
  { word: "docs", types: ["document", "file"] },
  { word: "video", types: ["video"] },
];
const typeOf = (c) => String(c?.data_type ?? "").toLowerCase();
const named = (columns, re) => (columns ?? []).some((c) => re.test(c?.name));

/**
 * One plain line for the dataset list (`Kind · count …`).
 * @param {{kind?: string, number_of_datapoints?: number}} row  list item
 * @param {{name: string, data_type: string}[]} [columns]  its columns
 * @returns {string}
 */
export function datasetKindLine(row, columns) {
  const n = Number(row?.number_of_datapoints ?? row?.numberOfDatapoints) || 0;
  const count = (noun) => fmtCount(n, noun);
  if (row?.kind === "task_suite")
    return `Task suite · ${count("task")} with end-state checks`;
  if (row?.kind === "scenarios")
    return `Voice · ${fmtCount(n, "simulated caller")}`;
  if (row?.kind === "conversations")
    return `Sessions · ${fmtCount(n, "production conversation")}`;
  const cols = columns ?? [];
  const chat =
    cols.some((c) => typeOf(c) === "conversation") ||
    named(cols, /conversation|messages/i);
  const media = MEDIA_WORDS.filter((m) =>
    cols.some((c) => m.types.includes(typeOf(c))),
  ).map((m) => m.word);
  if (media.length >= 2)
    return `Multimodal · ${[...(chat ? ["chat"] : []), ...media].join(", ")}`;
  if (chat) return `Conversations · ${count("row")}`;
  if (named(cols, /expected_tool_calls/i))
    return `Tool calls · ${count("row")} with expected calls`;
  if (named(cols, /^expected/i))
    return `Q&A · ${count("row")} with expected answers`;
  return `Rows · ${count("row")}`;
}

/**
 * the interface contract (the interface contract §5 A1, A6, A7): the organisation columns, a catalog of
 * their own so DATASET_LIST_COLUMNS (and its default view) is unchanged.
 * All hidden by default, listed under "Organisation" and "Metadata" in
 * View › Columns. The page concatenates them after DATASET_LIST_COLUMNS,
 * plus one `meta:<key>` column per metadata key (metadataKeyColumns).
 */
export const DATASET_ORG_COLUMNS = Object.freeze([
  {
    id: "folder",
    label: "Folder",
    defaultVisible: false,
    group: "organisation",
    groupable: true,
  },
  {
    id: "agents",
    label: "Agents",
    defaultVisible: false,
    group: "organisation",
    groupable: true,
  },
  {
    id: "metadata",
    label: "Metadata",
    defaultVisible: false,
    group: "metadata",
  },
]);

export const META_COLUMN_PREFIX = "meta:";

/** One hidden catalog column per metadata key: `meta:domain` "domain". */
export const metadataKeyColumns = (keys) =>
  [...new Set(keys ?? [])].sort().map((key) => ({
    id: `${META_COLUMN_PREFIX}${key}`,
    label: key,
    defaultVisible: false,
    group: "metadata",
    groupable: true,
  }));

/**
 * L1: the catalog on the real backend: only what get-datasets answers
 * (realBackendCaps REAL_LIST_COLUMNS), Name · Rows · Created shown, no
 * organisation or metadata columns. Pure.
 */
export const realDatasetListCatalog = () =>
  realListColumnIds().map((id) => {
    const col = DATASET_LIST_COLUMNS.find((c) => c.id === id);
    return {
      ...col,
      defaultVisible: REAL_LIST_DEFAULT_VISIBLE.includes(id),
    };
  });

/** The list's full catalog: the fixed columns, then the organisation ones. */
export const datasetListCatalog = (metadataKeys, real = datasetCaps().real) =>
  real
    ? realDatasetListCatalog()
    : [
        ...DATASET_LIST_COLUMNS,
        ...DATASET_ORG_COLUMNS,
        ...metadataKeyColumns(metadataKeys),
      ];

/** Column groups for View › Columns (ids the catalog's `group` uses). */
export const DATASET_COLUMN_GROUPS = Object.freeze([
  { id: "organisation", label: "Organisation" },
  { id: "metadata", label: "Metadata" },
]);

/**
 * L1 (the capability contract): an item whose action the host does not pass is not
 * shown (the real backend passes only the actions it serves); dividers
 * never lead, trail or repeat. Pure.
 */
export function tidyMenu(items) {
  const out = [];
  for (const it of items) {
    if (it.divider) {
      if (out.length && !out[out.length - 1].divider) out.push(it);
    } else if (typeof it.run === "function") out.push(it);
  }
  while (out.length && out[out.length - 1].divider) out.pop();
  return out;
}
