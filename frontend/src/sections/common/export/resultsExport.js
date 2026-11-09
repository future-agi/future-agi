// Results Download… (the interface contract, DI-09, E37): exactly the rows of the current
// results view — the page's runs, baseline, filters, show and sort — or
// every row of those runs. Read from R3 (`…/results/`) with the page's own
// view state (runResultsParams, the grid's param builder), paged, then each
// row once in full (untruncated outputs, judge reasons, every trial) and
// written by writers.js. The model is pure; `fetchResultsExport` is the
// only network code.
import axios, { endpoints } from "src/utils/axios";
import {
  RESULTS_PAGE_SIZE,
  runResultsParams,
} from "src/api/develop/run-results";
import { pillLabel } from "src/sections/common/Results/filterPills";
import {
  defaultSortFor,
  sortLabel,
} from "src/sections/experiment-detail/results/sortModel";
import { readResultsState } from "src/sections/experiment-detail/results/useResultsUrlState";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { exportFileName, toCsv, toJson, toJsonl } from "./writers";

/** Parallel full-row reads while exporting (keeps the page responsive). */
export const FULL_ROW_CONCURRENCY = 6;

/**
 * the interface contract: the results view the grid shows, read from the page's URL the
 * way RunResultsView reads it (`show` only with two or more runs; `sort`
 * the URL's, else the page default). `view` (when the page passes it)
 * wins, which also carries the grid's search box. A view read from the URL
 * is marked `source: "url"`: its search box is the grid's page state, so
 * the dialog takes it from the grid's own read (viewFromGridReads).
 */
export function resultsViewOf({ search, runIds, baselineId, view }) {
  if (view) return view;
  const url = readResultsState(new URLSearchParams(search));
  const sortParam = new URLSearchParams(search).get("sort");
  return {
    runIds,
    baselineId,
    pills: url.pills,
    show: runIds.length > 1 ? url.show : "all",
    search: "",
    sort: sortParam ?? defaultSortFor({ runCount: runIds.length, baselineId }),
    agg: url.agg,
    source: "url",
  };
}

const withoutSearchAndPage = ({
  search: _search,
  page: _page,
  page_size: _pageSize,
  ...rest
} = {}) => rest;
const sameParams = (a, b) => {
  const ka = Object.keys(a).sort();
  const kb = Object.keys(b).sort();
  return (
    ka.length === kb.length &&
    ka.every((k, i) => k === kb[i] && String(a[k]) === String(b[k]))
  );
};

/**
 * The view the grid is showing, for a view read from the URL. The grid's
 * search box is page state, so the search comes from the grid's own live
 * R3 reads (`gridParams`, the params of the active grid reads): of the
 * reads of these runs, the ones whose other keys equal the URL view.
 * - no live read of these runs (no grid on the page): the URL view;
 * - one search among the matching reads: the URL view with that search;
 * - none or several: null, so the dialog offers no "This view" whose
 *   count could differ from the grid.
 * A view the page passed itself (not `source: "url"`) is returned as is.
 * @param {object|null} urlView
 * @param {object[]} gridParams
 */
export function viewFromGridReads(urlView, gridParams) {
  if (!urlView || urlView.source !== "url") return urlView ?? null;
  const want = runResultsParams({ ...urlView, search: "" });
  const live = (gridParams ?? []).filter((p) => p && p.runs === want.runs);
  if (!live.length) return urlView;
  const searches = [
    ...new Set(
      live
        .filter((p) => sameParams(withoutSearchAndPage(p), want))
        .map((p) => p.search ?? ""),
    ),
  ];
  if (searches.length !== 1) return null;
  return { ...urlView, search: searches[0], source: "grid" };
}

/** The "every row" view of the same runs and baseline: no filter, row order. */
export function everyRowView(view) {
  return {
    runIds: view?.runIds ?? [],
    baselineId: view?.baselineId,
    agg: view?.agg,
    pills: [],
    show: "all",
    search: "",
    sort: "order",
  };
}

/** The sort a view really uses (the page's default when the URL has none). */
export function effectiveSort(view) {
  return (
    view?.sort ??
    defaultSortFor({
      runCount: view?.runIds?.length ?? 0,
      baselineId: view?.baselineId,
    })
  );
}

const SHOW_TEXT = {
  regressed: "worse than baseline",
  improved: "better than baseline",
  changed: "changed vs baseline",
};

/**
 * Words for what a view narrows to: `Fails Correctness · #4A · Split:
 * smoke · sorted by Slowest · #4A`.
 * @param {object} view
 * @param {{runs?: object[], metrics?: object[], labels?: object}} ctx
 */
export function viewParts(view, { runs = [], metrics = [], labels } = {}) {
  const parts = (view?.pills ?? []).map((p) =>
    pillLabel(p, { runs, metrics, baselineId: view?.baselineId, labels }),
  );
  if (view?.show && view.show !== "all") parts.push(SHOW_TEXT[view.show]);
  if (view?.search) parts.push(`search "${view.search}"`);
  const sort = effectiveSort(view);
  // `worse than baseline` already orders nothing new: the default sort
  // (worse first) adds no words to it.
  const redundant = sort === "regressed" && view?.show === "regressed";
  if (sort && sort !== "order" && !redundant) {
    const metricsByKey = Object.fromEntries(metrics.map((m) => [m.key, m]));
    const runsById = Object.fromEntries(runs.map((r) => [r.run_id, r]));
    parts.push(
      sortLabel(sort, { metricsByKey, runsById })
        .replace(/^Sort: /, "sorted by ")
        .replace(
          /^sorted by worse than baseline first$/,
          "worse than baseline first",
        ),
    );
  }
  return parts.filter(Boolean);
}

/** Whether a view narrows or reorders the rows at all. */
export const viewIsNarrowed = (view) =>
  Boolean(
    (view?.pills ?? []).length ||
      (view?.show && view.show !== "all") ||
      view?.search ||
      (effectiveSort(view) ?? "order") !== "order",
  );

const runLabel = (run) => run?.label ?? run?.run_id ?? "";

/** The file header: who, what and how the rows were narrowed. */
export function resultsMeta({ dataset, view, summary, exportedAt, scope }) {
  const runs = summary?.runs ?? [];
  const metrics = summary?.metrics ?? [];
  const versions = Object.fromEntries(
    runs.map((r) => [runLabel(r), r.dataset_version?.number ?? null]),
  );
  const distinct = [...new Set(Object.values(versions))];
  const baseline = runs.find(
    (r) => r.run_id === (summary?.baseline ?? view?.baselineId),
  );
  return {
    dataset: dataset ?? null,
    dataset_version: distinct.length === 1 ? distinct[0] : versions,
    runs: runs.map((r) => ({
      run: runLabel(r),
      run_id: r.run_id,
      name: r.name ?? r.experiment_name ?? null,
      dataset_version: r.dataset_version?.number ?? null,
      // the capability contract: the run's real outcome (the one derivation), never its
      // stamp (a run whose rows all errored is Failed, never Completed).
      status: r.status == null ? null : getRunStatus(r).key,
    })),
    baseline: baseline ? runLabel(baseline) : null,
    eval_versions: Object.fromEntries(
      metrics
        .filter((m) => m.kind === "eval" || String(m.key).startsWith("eval:"))
        .map((m) => [
          m.name ?? m.key,
          m.versions
            ? Object.fromEntries(
                Object.entries(m.versions).map(([id, v]) => [
                  runLabel(runs.find((r) => r.run_id === id)) || id,
                  v,
                ]),
              )
            : m.version ?? null,
        ]),
    ),
    rows: scope === "all" ? "every row" : "this view",
    filters:
      scope === "all" ? [] : viewParts({ ...view, sort: "order" }, summary),
    filter_pills: scope === "all" ? [] : view?.pills ?? [],
    sort:
      scope === "all"
        ? "row order"
        : viewParts(
            {
              sort: effectiveSort(view),
              runIds: view?.runIds,
              baselineId: view?.baselineId,
            },
            summary,
          )[0] ?? "row order",
    exported_at: exportedAt,
  };
}

const evalMetrics = (summary) =>
  (summary?.metrics ?? []).filter(
    (m) => m.kind === "eval" || String(m.key).startsWith("eval:"),
  );
const humanMetrics = (summary) =>
  (summary?.metrics ?? []).filter(
    (m) => m.kind === "human" || String(m.key).startsWith("human:"),
  );

const firstTrace = (res) =>
  res?.trace_ids?.length
    ? res.trace_ids
    : (res?.trials_detail ?? []).map((t) => t.trace_id).filter(Boolean);

/** One run's result as the file shows it (JSON / JSONL). */
function runRecord(res, run, summary) {
  if (!res) return null;
  const evals = {};
  evalMetrics(summary).forEach((m) => {
    const s = res.scores?.[m.key];
    if (!s) return;
    evals[m.name ?? m.key] = {
      value: s.value ?? null,
      display: s.display ?? null,
      reason: s.reason ?? null,
      evaluator_version: s.evaluator_version ?? null,
      ...(Array.isArray(s.trials) && s.trials.length > 1
        ? { trials: s.trials }
        : {}),
      ...(s.override
        ? {
            human_override: {
              value: s.override.value ?? null,
              comment: s.override.comment || null,
              at: s.override.at ?? null,
            },
          }
        : {}),
    };
  });
  const human = Object.fromEntries(
    Object.entries(res.human ?? {}).map(([k, h]) => [
      humanMetrics(summary).find((m) => m.key === k)?.name ?? k,
      h?.value ?? null,
    ]),
  );
  return {
    run: runLabel(run),
    run_id: run?.run_id ?? null,
    dataset_version: run?.dataset_version?.number ?? null,
    status: res.status ?? null,
    output: res.output ?? null,
    error: res.error ?? null,
    trials: res.trials ?? null,
    latency_ms: res.latency_ms ?? null,
    tokens: res.tokens ?? null,
    cost: res.cost ?? null,
    trace_ids: firstTrace(res),
    evals,
    ...(Object.keys(human).length ? { human_labels: human } : {}),
    ...(res.trials_detail?.length > 1
      ? {
          trials_detail: res.trials_detail.map((t) => ({
            trial: t.trial,
            status: t.status ?? null,
            output: t.output ?? null,
            error: t.error ?? null,
            latency_ms: t.latency_ms ?? null,
            cost: t.cost ?? null,
            trace_id: t.trace_id ?? null,
            evals: Object.fromEntries(
              Object.entries(t.scores ?? {}).map(([k, s]) => [
                evalMetrics(summary).find((m) => m.key === k)?.name ?? k,
                { value: s?.value ?? null, reason: s?.reason ?? null },
              ]),
            ),
          })),
        }
      : {}),
  };
}

/**
 * Records for JSON / JSONL (one per grid row, runs keyed by label) and flat
 * columns + rows for CSV. `layout` "row" = one line per grid row (wide,
 * the grid's order); "run" = one line per row and run (long).
 * @param {{rows: object[], summary: object, layout?: "row"|"run"}} p
 */
export function resultsRecords({ rows, summary, layout = "row" }) {
  const runs = summary?.runs ?? [];
  const baseline = runs.find((r) => r.run_id === summary?.baseline);
  const evals = evalMetrics(summary);
  const inputKeys = [];
  const expectedKeys = [];
  const metaKeys = [];
  const push = (list, k) => !list.includes(k) && list.push(k);
  (rows ?? []).forEach((r) => {
    Object.keys(r.inputs ?? {}).forEach((k) => push(inputKeys, k));
    Object.keys(r.expected?.value ?? {}).forEach((k) => push(expectedKeys, k));
    Object.keys(r.metadata ?? {}).forEach((k) => push(metaKeys, k));
  });
  const humanKeys = [];
  (rows ?? []).forEach((r) =>
    Object.values(r.results ?? {}).forEach((res) =>
      Object.keys(res?.human ?? {}).forEach((k) => push(humanKeys, k)),
    ),
  );
  const humanName = (k) =>
    humanMetrics(summary).find((m) => m.key === k)?.name ?? k;

  const records = (rows ?? []).map((r) => ({
    row_id: r.example_id,
    row: Number.isFinite(r.row_order) ? r.row_order : null,
    splits: r.splits ?? [],
    inputs: r.inputs ?? {},
    expected: r.expected?.value ?? {},
    metadata: r.metadata ?? {},
    baseline_run: baseline ? runLabel(baseline) : null,
    runs: Object.fromEntries(
      runs
        .map((run) => [
          runLabel(run),
          runRecord(r.results?.[run.run_id], run, summary),
        ])
        .filter(([, v]) => v),
    ),
    ...(r.preference ? { preference: r.preference } : {}),
  }));

  const shared = [
    { key: "row_id", label: "row_id" },
    { key: "row", label: "row" },
    { key: "splits", label: "splits" },
    { key: "dataset_version", label: "dataset_version" },
    { key: "baseline_run", label: "baseline_run" },
    ...inputKeys.map((k) => ({ key: `in:${k}`, label: k })),
    ...expectedKeys.map((k) => ({ key: `ex:${k}`, label: `expected.${k}` })),
    ...metaKeys.map((k) => ({ key: `md:${k}`, label: `metadata.${k}` })),
  ];
  const perRun = (prefix, { version = false } = {}) => [
    ...(version
      ? [{ key: `${prefix}dataset_version`, label: `${prefix}dataset_version` }]
      : []),
    { key: `${prefix}output`, label: `${prefix}output` },
    { key: `${prefix}status`, label: `${prefix}status` },
    { key: `${prefix}trials`, label: `${prefix}trials` },
    { key: `${prefix}latency_ms`, label: `${prefix}latency_ms` },
    { key: `${prefix}tokens`, label: `${prefix}tokens` },
    { key: `${prefix}cost`, label: `${prefix}cost` },
    { key: `${prefix}trace_ids`, label: `${prefix}trace_ids` },
    ...evals.flatMap((m) => [
      { key: `${prefix}e:${m.key}`, label: `${prefix}${m.name ?? m.key}` },
      {
        key: `${prefix}er:${m.key}`,
        label: `${prefix}${m.name ?? m.key} reason`,
      },
    ]),
    ...humanKeys.map((k) => ({
      key: `${prefix}h:${k}`,
      label: `${prefix}${humanName(k)}`,
    })),
  ];
  const fillRun = (out, prefix, rec) => {
    if (!rec) return;
    out[`${prefix}output`] = rec.output;
    out[`${prefix}status`] = rec.status;
    out[`${prefix}trials`] = rec.trials;
    out[`${prefix}latency_ms`] = rec.latency_ms;
    out[`${prefix}tokens`] = rec.tokens;
    out[`${prefix}cost`] = rec.cost;
    out[`${prefix}trace_ids`] = (rec.trace_ids ?? []).join(" ");
    evals.forEach((m) => {
      const e = rec.evals?.[m.name ?? m.key];
      out[`${prefix}e:${m.key}`] = e ? e.display ?? e.value : null;
      out[`${prefix}er:${m.key}`] = e?.reason ?? null;
    });
    humanKeys.forEach((k) => {
      out[`${prefix}h:${k}`] = rec.human_labels?.[humanName(k)] ?? null;
    });
  };
  const sharedValues = (rec) => {
    const out = {
      row_id: rec.row_id,
      row: rec.row,
      splits: (rec.splits ?? []).join(";"),
      baseline_run: rec.baseline_run,
    };
    inputKeys.forEach((k) => (out[`in:${k}`] = rec.inputs?.[k] ?? null));
    expectedKeys.forEach((k) => (out[`ex:${k}`] = rec.expected?.[k] ?? null));
    metaKeys.forEach((k) => (out[`md:${k}`] = rec.metadata?.[k] ?? null));
    return out;
  };

  if (layout === "run") {
    const flat = [
      ...shared.slice(0, 5),
      { key: "run", label: "run" },
      ...shared.slice(5),
      ...perRun(""),
    ];
    const flatRows = [];
    records.forEach((rec) =>
      Object.values(rec.runs).forEach((run) => {
        const out = { ...sharedValues(rec), run: run.run };
        out.dataset_version = run.dataset_version;
        fillRun(out, "", run);
        flatRows.push(out);
      }),
    );
    const lines = records.flatMap((rec) =>
      Object.values(rec.runs).map((run) => {
        const { runs: _runs, ...rest } = rec;
        return { ...rest, dataset_version: run.dataset_version, ...run };
      }),
    );
    return { records: lines, flat, flatRows };
  }

  // One dataset version for every run: one `dataset_version` column. Runs
  // on different versions (a compare across versions): no shared column;
  // each run's columns start with its own `<run> dataset_version`, and the
  // JSON line carries the per-run map, as the header does.
  const versionOf = (run) => run.dataset_version?.number ?? null;
  const versions = [...new Set(runs.map(versionOf))];
  const oneVersion = versions.length <= 1;
  const versionMap = Object.fromEntries(
    runs.map((run) => [runLabel(run), versionOf(run)]),
  );
  const flat = [
    ...(oneVersion
      ? shared
      : shared.filter((c) => c.key !== "dataset_version")),
    ...runs.flatMap((run) =>
      perRun(`${runLabel(run)} `, { version: !oneVersion }),
    ),
  ];
  const flatRows = records.map((rec) => {
    const out = sharedValues(rec);
    if (oneVersion) out.dataset_version = versions[0] ?? null;
    runs.forEach((run) => {
      const prefix = `${runLabel(run)} `;
      if (!oneVersion) out[`${prefix}dataset_version`] = versionOf(run);
      fillRun(out, prefix, rec.runs[runLabel(run)]);
    });
    return out;
  });
  const withVersion = records.map((rec) => ({
    ...rec,
    dataset_version: oneVersion ? versions[0] ?? null : versionMap,
  }));
  return { records: withVersion, flat, flatRows };
}

/** One sentence for the CSV `# …` context line. */
export function contextLineOf(meta) {
  const runs = (meta?.runs ?? []).map((r) => r.run).join(", ");
  const version =
    typeof meta?.dataset_version === "object" && meta?.dataset_version
      ? Object.entries(meta.dataset_version)
          .map(([r, v]) => `${r} v${v}`)
          .join(", ")
      : meta?.dataset_version != null
        ? `v${meta.dataset_version}`
        : "";
  return [
    meta?.dataset,
    version && `dataset ${version}`,
    runs && `runs ${runs}`,
    meta?.baseline && `baseline ${meta.baseline}`,
    meta?.filters?.length ? meta.filters.join(" · ") : meta?.rows,
    meta?.sort && meta.sort !== "row order" ? meta.sort : null,
    meta?.exported_at && `exported ${meta.exported_at}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The results file for one format: `{data, mime, name}`. */
export function resultsFile({
  format,
  records,
  flat,
  flatRows,
  meta,
  contextLine = false,
  nameParts,
}) {
  let data;
  if (format === "json") data = toJson({ meta, rows: records });
  else if (format === "jsonl") data = toJsonl(records);
  else
    data = toCsv(flatRows, flat, {
      contextLine: contextLine ? contextLineOf(meta) : undefined,
    });
  return {
    data,
    mime:
      format === "json"
        ? "application/json"
        : format === "jsonl"
          ? "application/x-ndjson"
          : "text/csv",
    name: exportFileName(nameParts, format),
  };
}

/** Run `fn` over `items` with at most `limit` in flight; keeps the order. */
export async function mapLimit(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next;
      next += 1;
      // eslint-disable-next-line no-await-in-loop
      out[i] = await fn(items[i], i);
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, worker),
  );
  return out;
}

/**
 * Every row of a view, in the view's order: R3 pages (the grid's params),
 * then each row in full (untruncated outputs, judge reasons, trials).
 * Returns `{rows, summary, total}`.
 * @param {string} datasetId
 * @param {object} view  {runIds, baselineId, pills, show, search, sort, agg}
 * @param {{onProgress?: Function, pageSize?: number}} [opts]
 */
export async function fetchResultsExport(
  datasetId,
  view,
  { onProgress, pageSize = RESULTS_PAGE_SIZE } = {},
) {
  const params = runResultsParams({ ...view, sort: effectiveSort(view) });
  const url = endpoints.develop.datasetRuns.results(datasetId);
  const rows = [];
  let summary = null;
  let total = null;
  for (let page = 0; ; page += 1) {
    // eslint-disable-next-line no-await-in-loop
    const res = await axios.get(url, {
      params: { ...params, page, page_size: pageSize },
    });
    const r = res?.data?.result ?? {};
    if (page === 0) {
      summary = r.summary ?? null;
      total = r.total ?? 0;
    }
    rows.push(...(r.rows ?? []));
    onProgress?.({ stage: "rows", done: rows.length, total });
    if (!(r.rows ?? []).length || rows.length >= (total ?? 0)) break;
  }
  let done = 0;
  const full = await mapLimit(rows, FULL_ROW_CONCURRENCY, async (row) => {
    const res = await axios.get(url, {
      params: { ...params, example_id: row.example_id, full: true },
    });
    done += 1;
    onProgress?.({ stage: "full", done, total: rows.length });
    return res?.data?.result?.rows?.[0] ?? row;
  });
  return { rows: full, summary, total: total ?? full.length };
}
