/**
 * Cross-component selection state for scenarios in the workspace.
 *
 * The scenario table lives inside ScenariosStep; the builder chat lives
 * inside EnvironmentWorkspace one layer up. When a user selects rows in
 * the table and types in the chat, the chat needs to know so it can
 * treat the message as a bulk edit against those specific rows.
 *
 * State: { ids: string[], rows: object[] }. `rows` carries the full
 * scenario objects so the chat can name them in its reply without
 * cross-referencing the store.
 */

const listeners = new Set();
let current = { ids: [], rows: [] };

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
  current = {
    ids: Array.isArray(next?.ids) ? next.ids : [],
    rows: Array.isArray(next?.rows) ? next.rows : [],
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
