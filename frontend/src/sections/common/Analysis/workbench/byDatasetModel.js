// By dataset (R2-SPEC §3.3.7): one config across every dataset it ran on.
// Pure join over R1 `include=metrics,config` per dataset (useByDataset):
// rows = datasets, columns = a config axis, each cell = that run's value
// on its dataset's headline measure. No statistics across rows.
import { configKeyOf } from "src/api/develop/r2";
import { runKindOf } from "src/sections/common/Runs/runKind";
import { wilson } from "src/sections/common/Analysis/runStats";
import { runAxisValues } from "./workbenchPivot";

export const BY_AXES = Object.freeze({
  agent_version: "Agent version",
  prompt_version: "Prompt version",
  harness: "Harness",
  model: "Model",
  code_repo: "Code repo",
});

const DONE = new Set(["Completed", "PartialCompleted", "Cancelled"]);

/** Default column axis for a focused run (agents → Agent version). */
export function defaultByAxis(run) {
  const kind = runKindOf(run);
  if (kind === "agent") return "agent_version";
  if (kind === "prompt") return "prompt_version";
  if (kind === "harness") return "harness";
  if (kind === "code") return "code_repo";
  return "model";
}

/** The setup's name without its version (`Support Agent`, `Claude Code × …`). */
export function configName(run) {
  const c = run?.config ?? {};
  if (c.agent?.name) return c.agent.name;
  if (c.prompt?.name)
    return `${c.prompt.name}${c.model?.id ? ` × ${c.model.id}` : ""}`;
  if (c.harness?.name)
    return `${c.harness.name}${c.harness.model ? ` × ${c.harness.model}` : ""}`;
  if (c.code?.repo) return c.code.repo;
  return run?.name ?? "This setup";
}

/** Column label of a run on an axis: `Support Agent v3`, `V4`, … */
export function axisLabel(run, axis) {
  return runAxisValues(run)[axis] ?? "Not set";
}

/** The dataset's headline measure: the most common first metric of its runs. */
export function headlineKey(runs) {
  const count = new Map();
  (runs ?? []).forEach((r) => {
    const k = r.metric_keys?.[0];
    if (k) count.set(k, (count.get(k) ?? 0) + 1);
  });
  return [...count.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

const newestFirst = (a, b) =>
  String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")) ||
  String(a.label ?? "").localeCompare(String(b.label ?? ""));

/**
 * @param {Array<{dataset: object, runs: object[]}>} entries  every run per dataset
 * @param {{configKey: string, axis: string, extraKeys?: string[],
 *   chosen?: Object<string, string>, focusValue?: string}} opts  `chosen` =
 *   `<datasetId>|<col>` → run id; `focusValue` = the focused run's axis value
 * @returns {{columns: Array<{key, label, configKey}>, rows: object[], ranOn: number}}
 */
export function buildByDataset(
  entries,
  { configKey, axis, extraKeys = [], chosen = {}, focusValue },
) {
  const all = (entries ?? []).flatMap((e) =>
    e.runs.map((r) => ({
      ...r,
      dataset_id: r.dataset_id ?? e.dataset.datasetId,
    })),
  );
  const focus = all.filter(
    (r) => configKeyOf(r) === configKey && r.listed !== false,
  );
  const colKeys = [...new Set(focus.map((r) => axisLabel(r, axis)))].sort(
    (a, b) => a.localeCompare(b, "en", { numeric: true }),
  );
  const columns = [
    ...colKeys.map((k) => ({
      key: `${configKey}#${k}`,
      label: k,
      configKey,
      axisValue: k,
    })),
    ...extraKeys
      .filter((k) => k !== configKey)
      .map((k) => {
        const any = all.find((r) => configKeyOf(r) === k);
        return any
          ? { key: k, label: configName(any), configKey: k, axisValue: null }
          : null;
      })
      .filter(Boolean),
  ];
  const rows = (entries ?? [])
    .map((e) => {
      const dsId = e.dataset.datasetId;
      const measureKey = headlineKey(e.runs);
      const cells = {};
      columns.forEach((col) => {
        const runs = e.runs
          .filter(
            (r) =>
              r.listed !== false &&
              configKeyOf(r) === col.configKey &&
              (col.axisValue == null || axisLabel(r, axis) === col.axisValue),
          )
          .sort(newestFirst);
        if (!runs.length) return;
        const done = runs.filter((r) => DONE.has(r.status));
        const pickId = chosen[`${dsId}|${col.key}`];
        const run = runs.find((r) => r.run_id === pickId) ?? done[0] ?? runs[0];
        const agg = run.metrics?.aggregates?.[measureKey] ?? null;
        cells[col.key] = {
          run,
          others: runs.filter((r) => r.run_id !== run.run_id),
          value: Number.isFinite(agg?.avg) ? agg.avg : null,
          n: agg?.n ?? 0,
          sum: agg?.sum ?? null,
          trials: run.trials ?? 1,
        };
      });
      return { dataset: e.dataset, measureKey, cells };
    })
    .filter((r) => Object.keys(r.cells).length > 0);
  const focusCol =
    columns.find((c) => c.axisValue != null && c.axisValue === focusValue) ??
    columns[0];
  return {
    columns,
    rows,
    ranOn: rows.filter((r) => focusCol && r.cells[focusCol.key]).length,
    focusCol,
  };
}

/**
 * `80.3% · #3A` · `82.1% · #4A, 3 trials` (trials differ across the row) ·
 * `66.7% (12 of 18) · #1A` (under 30 single-trial rows).
 */
export function byCellText(cell, rowCells, { rate = true } = {}) {
  if (!cell?.run) return "—";
  if (cell.value == null) return `Not scored · ${cell.run.label}`;
  const v = rate ? `${(cell.value * 100).toFixed(1)}%` : cell.value.toFixed(2);
  const few =
    rate &&
    cell.trials === 1 &&
    cell.n > 0 &&
    cell.n < 30 &&
    Number.isFinite(cell.sum)
      ? ` (${Math.round(cell.sum)} of ${cell.n})`
      : "";
  const trialSet = new Set(Object.values(rowCells ?? {}).map((c) => c.trials));
  const trials =
    cell.trials > 1 && trialSet.size > 1 ? `, ${cell.trials} trials` : "";
  return `${v}${few} · ${cell.run.label}${trials}`;
}

/** A pivot-shaped object for GroupBarsChart (groups = datasets). */
export function byDatasetPivot(model) {
  const groups = model.rows.map((r) => r.dataset.name);
  const cells = {};
  const rowTotals = {};
  model.rows.forEach((r) => {
    cells[r.dataset.name] = {};
    model.columns.forEach((c) => {
      const cell = r.cells[c.key];
      const n = (cell?.n ?? 0) * (cell?.trials ?? 1);
      cells[r.dataset.name][c.key] = cell
        ? {
            value: cell.value,
            n,
            rows: cell.n,
            pass: cell.value != null ? Math.round(cell.value * n) : undefined,
            ci: cell.value != null && n ? wilson(cell.value, n) : null,
            byCell: cell,
          }
        : { value: null, n: 0, rows: 0 };
    });
    rowTotals[r.dataset.name] = {
      rows: Math.max(...model.columns.map((c) => r.cells[c.key]?.n ?? 0)),
    };
  });
  return {
    groups,
    pivot: {
      columns: model.columns.map((c) => ({ ...c, runIds: [] })),
      cells,
      rowTotals,
      failedEval: false,
    },
  };
}

/**
 * HO R8 (W5 B-H7): `Save as leaderboard…` from By dataset. The board keeps
 * every dataset this setup ran on, the runs rule `config_key = "<key>"`
 * (the runs catalogue's field, the same key as this view), and each
 * dataset's headline measure as a column when the leaderboard can rank it
 * (`columnKeys`, the metric keys of its column options). Pure.
 * @param {{rows: Array, focusCol?: object}} model buildByDataset()
 * @param {{configKey: string, name?: string, columnKeys?: Set<string>}} o
 * @returns {{name, dataset_ids, run_filter, columns, sort}|null}
 */
export function leaderboardDraftOf(
  model,
  { configKey, name, columnKeys } = {},
) {
  if (!configKey || !model?.rows?.length) return null;
  const ran = model.rows.filter((r) =>
    Object.values(r.cells ?? {}).some(
      (c) => c?.run && configKeyOf(c.run) === configKey,
    ),
  );
  if (!ran.length) return null;
  const measures = [...new Set(ran.map((r) => r.measureKey).filter(Boolean))];
  const columns = measures
    .filter((k) => !columnKeys || columnKeys.has(k))
    .map((key) => ({ key }));
  return {
    name: `${name ?? "This setup"} ${ran.length > 1 ? "across datasets" : "runs"}`,
    dataset_ids: ran.map((r) => r.dataset.datasetId),
    run_filter: `config_key = ${JSON.stringify(configKey)}`,
    columns,
    sort: null,
  };
}
