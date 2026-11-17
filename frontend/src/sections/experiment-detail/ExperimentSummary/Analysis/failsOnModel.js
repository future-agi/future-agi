// 8c: one `Fails on` card per run in the Summary: the
// categories and tasks it fails most, counted from the Matrix read (the same
// cells as every other screen), plus Falcon's top cause for that run. Pure.
import {
  groupValueOf,
  rowPassRate,
} from "src/sections/common/Analysis/runStats";

const trialsOf = (cell) => (Array.isArray(cell?.trials) ? cell.trials : []);
const failedTries = (cell) =>
  trialsOf(cell).filter((t) => t === 0 || t === false || t === "error").length;

/**
 * @param {object} matrix  R3 shape=matrix
 * @param {{runIds: string[], causes?: object[], top?: number,
 *   groupKey?: string}} opts
 * @returns {{runId, failing: number, total: number, categories: {name,
 *   count, rowIds}[], tasks: {id, label, failed, tries}[], cause: object|null,
 *   failingIds: string[], errored?: {ids: string[], categories: {name,
 *   count, rowIds}[], tasks: {id, label}[]}}[]}  `errored`: only for a run
 *   with nothing to count, its rows with an errored try (it fails none).
 */
export function failsOnModel(
  matrix,
  { runIds, causes = [], top = 3, groupKey = "category" } = {},
) {
  const rows = matrix?.rows ?? [];
  return (runIds ?? []).map((runId) => {
    // A run with nothing to count (its column's `pass_fail_evals` is 0) has
    // no row rate, so it fails no task here; its card shows its errored rows.
    const counts = matrix?.columns?.[runId]?.pass_fail_evals !== 0;
    const failing = rows.filter((r) => {
      const cell = r.cells?.[runId];
      const rate = cell && counts ? rowPassRate(trialsOf(cell)) : null;
      return rate != null && rate < 1;
    });
    const total = rows.filter((r) => r.cells?.[runId]).length;
    const categoriesOf = (list) => {
      const byCat = new Map();
      list.forEach((r) => {
        const name = groupValueOf(r, groupKey);
        if (!name || name === "—") return;
        if (!byCat.has(name)) byCat.set(name, []);
        byCat.get(name).push(r.row_id ?? r.id);
      });
      return [...byCat.entries()]
        .map(([name, ids]) => ({ name, count: ids.length, rowIds: ids }))
        .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
        .slice(0, top);
    };
    const categories = categoriesOf(failing);
    const tasks = failing
      .map((r) => ({
        id: r.row_id ?? r.id,
        label: r.label ?? null,
        n: r.n,
        failed: failedTries(r.cells[runId]),
        passed: trialsOf(r.cells[runId]).filter((t) => t === 1 || t === true)
          .length,
        tries: trialsOf(r.cells[runId]).length,
      }))
      .sort((a, b) => b.failed - a.failed || (a.n ?? 0) - (b.n ?? 0))
      .slice(0, top);
    const cause =
      causes.find((c) => c.run_id === runId && c.status !== "rejected") ?? null;
    // Such a run's errored rows stay reachable from its card (no figure).
    const erroredRows = counts
      ? []
      : rows.filter((r) =>
          trialsOf(r.cells?.[runId]).some((t) => t === "error"),
        );
    return {
      runId,
      failing: failing.length,
      total,
      categories,
      tasks,
      cause,
      failingIds: failing.map((r) => r.row_id ?? r.id),
      ...(counts
        ? {}
        : {
            errored: {
              ids: erroredRows.map((r) => r.row_id ?? r.id),
              categories: categoriesOf(erroredRows),
              tasks: [...erroredRows]
                .sort((a, b) => (a.n ?? 0) - (b.n ?? 0))
                .slice(0, top)
                .map((r) => ({
                  id: r.row_id ?? r.id,
                  label: r.label ?? null,
                  n: r.n,
                })),
            },
          }),
    };
  });
}
