// W6 C4 (W6-10): run a cell, a row or a column of the Matrix again (pure).
// A cell or a row amends the runs that own those cells (rerun-cells
// `cells`: each cell gets one more result in its History); a column starts
// a new run of that config (rerun-cells `source_ids`: #2C → #2C·2) and the
// run it started from keeps its results. Nothing is overwritten. The new
// run's name comes from the one label rule (rerunCopyModel nextRunLabel);
// "vs #2C: 3 better · 1 worse" comes from the Matrix compare model
// (columnStats against the run it was started from).
import { nextRunLabel } from "src/sections/common/Runs/rerunCopyModel";
import { canRerunHere } from "src/sections/common/Runs/runKind";
import { columnStats } from "src/sections/experiment-detail/matrix/matrixModel";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const rowWord = (n, noun = "row") => plural(n, noun);

/** True when Future AGI can start this run's cells again itself. */
export const runnable = (run) =>
  Boolean(run?.experiment_id && run?.output_column_id) && canRerunHere(run);

/** The column header item: "Run #2C again" · "Creates #2C·2 on 30 rows". */
export function columnRunItem(run, { rows = 0, noun = "row" } = {}) {
  if (!run?.label || !run?.experiment_id || !canRerunHere(run)) return null;
  const next = nextRunLabel(run);
  return {
    label: `Run ${run.label} again`,
    secondary: `Creates ${next} on ${rowWord(rows, noun)}; ${run.label} keeps its results`,
    next,
    body: { source_ids: [run.run_id ?? run.id] },
    experimentId: run.experiment_id,
  };
}

/** The cell item: one cell of one run, amended with History. */
export function cellRunItem(run, row) {
  if (!runnable(run) || !row?.row_id) return null;
  return {
    label: "Run this cell again",
    secondary: `${run.label} on ${row.label ?? "this row"} · the result is added to its History`,
    experimentId: run.experiment_id,
    body: { cells: [{ column_id: run.output_column_id, row_id: row.row_id }] },
  };
}

/**
 * The row item: the row on every run in view that can run here, one
 * request per experiment (each run gets one amendment, scoped to the row).
 */
export function rowRunItem(runs, row) {
  const ok = (runs ?? []).filter(runnable);
  if (!ok.length || !row?.row_id) return null;
  const byExperiment = new Map();
  ok.forEach((run) => {
    const list = byExperiment.get(run.experiment_id) ?? [];
    list.push({ column_id: run.output_column_id, row_id: row.row_id });
    byExperiment.set(run.experiment_id, list);
  });
  return {
    label: `Run ${row.label ?? "this row"} on every run`,
    secondary: `${plural(ok.length, "run")} (${ok.map((r) => r.label).join(", ")}) · each adds a result to its History`,
    requests: [...byExperiment.entries()].map(([experimentId, cells]) => ({
      experimentId,
      body: { cells },
    })),
  };
}

/** The run a kept run was started from, when it is a later run (#2C·2). */
export function parentOf(run, runsById = {}) {
  if (!run || (Number(run.attempt) || 1) < 2 || !run.parent_run_id) return null;
  return runsById[run.parent_run_id] ?? null;
}

/**
 * "vs #2C: 3 better · 1 worse" for a run on a matrix that holds it and its
 * parent (rows compared by pass rate, the Matrix's own rule).
 */
export function vsParentLine(matrix, run, parent) {
  if (!matrix || !run || !parent) return null;
  const id = run.run_id ?? run.id;
  const pid = parent.run_id ?? parent.id;
  const s = columnStats(matrix, id, pid);
  const same = s.better === 0 && s.worse === 0;
  return same
    ? `vs ${parent.label}: same on every row`
    : `vs ${parent.label}: ${s.better} better · ${s.worse} worse`;
}

/** The toast of an amendment answer (`amended.summary`) or a started run. */
export function runToast(result, fallback) {
  const r = result?.data?.result ?? result ?? {};
  if (Array.isArray(r.labels) && r.labels.length)
    return `Started ${r.labels.join(", ")}.`;
  return r.amended?.summary ?? r.message ?? fallback;
}
