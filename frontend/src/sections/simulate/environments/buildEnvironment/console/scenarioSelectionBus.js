/**
 * Cross-component selection state for scenarios in the workspace.
 *
 * The scenario table lives inside ScenariosStep; the builder chat lives
 * inside EnvironmentWorkspace one layer up. When a user selects rows in
 * the table and types in the chat, the chat needs to know so it can
 * treat the message as a bulk edit against those specific rows.
 *
 * State: { ids, rows, count, all, jobId, predicate }.
 * `predicate` snapshots { search, filters, marked } for all-matching selections;
 * marked contains excluded row ids. `jobId` binds that scope to its environment.
 * `rows` carries the full scenario objects so the chat can name them in its
 * reply without cross-referencing the store. `count`/`all` describe an
 * all-matching (predicate) selection, where `ids` is empty but a whole page
 * of matches is selected. Chat resolves the predicate before sending; subscribers
 * (the console chip, the header run
 * yield) read `count` so an all-mode selection still registers.
 */

const listeners = new Set();
let current = { ids: [], rows: [], count: 0, all: false };

export function subscribeScenarioSelection(fn) {
  listeners.add(fn);
  /* Fire the current state on subscribe so a late subscriber picks
     up an existing selection instead of missing it. */
  try {
    fn(current);
  } catch {
    /* no-op */
  }
  return () => {
    listeners.delete(fn);
  };
}

export function publishScenarioSelection(next) {
  const ids = Array.isArray(next?.ids) ? next.ids : [];
  current = {
    ids,
    rows: Array.isArray(next?.rows) ? next.rows : [],
    // count falls back to ids.length for an include-mode selection; an all-mode
    // publisher passes an explicit count (ids is empty there).
    count: Number.isFinite(next?.count) ? next.count : ids.length,
    all: !!next?.all,
    jobId: next?.jobId,
    predicate: next?.predicate ? {
      search: next.predicate.search,
      filters: Object.fromEntries(Object.entries(next.predicate.filters || {}).map(
        ([field, values]) => [field, Array.isArray(values) ? [...values] : values],
      )),
      marked: [...(next.predicate.marked || [])],
    } : null,
  };
  listeners.forEach((fn) => {
    try {
      fn(current);
    } catch {
      /* no-op */
    }
  });
}

export function getScenarioSelection() {
  return current;
}

export function clearScenarioSelection() {
  publishScenarioSelection({ ids: [], rows: [] });
}
