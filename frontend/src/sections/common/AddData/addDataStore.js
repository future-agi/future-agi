// The Add-data sheet's open state (R2-SPEC P5). One global sheet is mounted
// in routes/sections/dashboard.jsx; every entry point (Observe bulk bars,
// trace drawers, Add row, New dataset, the annotate workspace, Observe
// compare, the task-suite dialog) opens it with openAddData(...). Zustand;
// not persisted and not part of resetAllStates().
//
// Frozen API (R2 manifest row 26): useAddDataStore, openAddData,
// closeAddData, ADD_DATA_SOURCE_KINDS, ADD_DATA_SHAPES.
import { create } from "zustand";

/** Where the data comes from. */
export const ADD_DATA_SOURCE_KINDS = Object.freeze([
  "traces", // {kind, project_id, trace_ids[]} (Observe traces / spans)
  "sessions", // {kind, project_id, session_ids[]}
  "file", // {kind, file_name?} (CSV / JSONL / JSON / YAML upload)
  "rows", // {kind} typed by hand (Add row)
  "experiment", // {kind, dataset_id, run_id, row_ids[]} (results → rows)
  "queue", // {kind, queue_id, item_ids[]} (annotate workspace)
  "simulation", // {kind, run_id, call_ids[]}
]);

/** What each item becomes in the target dataset. */
export const ADD_DATA_SHAPES = Object.freeze([
  "rows", // one row per trace / file line
  "conversation", // one conversation row per session
  "task", // one task row per session (task suites)
]);

const CLOSED = { open: false, source: null, target: null, shape: null };

/**
 * State: `{open, source, target, shape}`.
 * - `source`: `{kind, project_id?, trace_ids?, session_ids?, …}` (null = the
 *   sheet asks where the data comes from)
 * - `target`: `{dataset_id?, new_dataset?: {name, kind}}` (null = the sheet
 *   asks)
 * - `shape`: one of ADD_DATA_SHAPES or null (the sheet picks by source)
 * Actions: `openSheet({source, target, shape})`, `close()`.
 */
export const useAddDataStore = create((set) => ({
  ...CLOSED,
  openSheet: ({ source = null, target = null, shape = null } = {}) =>
    set({ open: true, source, target, shape }),
  close: () => set({ ...CLOSED }),
}));

/** Open the global Add-data sheet (callable outside React). */
export function openAddData(opts = {}) {
  useAddDataStore.getState().openSheet(opts);
}

/** Close the global Add-data sheet. */
export function closeAddData() {
  useAddDataStore.getState().close();
}
