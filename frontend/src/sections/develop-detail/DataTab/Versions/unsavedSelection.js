// Which of the rows an experiment is about to run on only exist as pending
// changes (IT-R1 G5 P1). Door rows (`Re-test these rows`, a blank row) carry
// temp ids (`new-N`, `imp-N`) until Save version maps them to real ids; an
// experiment pins a saved version, so those rows cannot run yet. Pure.

import { isSurfaceShown } from "src/launch/launchSwitch";

const pendingAdds = (ops) =>
  (ops ?? []).filter((o) => o?.op === "add_row" && o.temp_id);

const rowsWord = (n) => `${n} selected ${n === 1 ? "row" : "rows"}`;

/**
 * `{total, unsaved}` of the selection: the door's `rowIds` when there are
 * any, else the chosen splits (saved rows + pending rows that go into them).
 * `All rows` never counts pending rows (they are simply not in the version).
 */
export function unsavedSelection({ ops, rowIds, splitIds, splits } = {}) {
  const adds = pendingAdds(ops);
  const ids = (rowIds ?? []).filter(Boolean);
  if (ids.length) {
    const temp = new Set(adds.map((o) => o.temp_id));
    return {
      total: ids.length,
      unsaved: ids.filter((id) => temp.has(id)).length,
    };
  }
  const chosen = splitIds ?? [];
  if (!chosen.length) return { total: 0, unsaved: 0 };
  const unsaved = adds.filter((o) =>
    (o.split_ids ?? []).some((s) => chosen.includes(s)),
  ).length;
  const saved = (splits ?? [])
    .filter((s) => chosen.includes(s.id))
    .reduce((n, s) => n + (Number(s.row_count) || 0), 0);
  return { total: saved + unsaved, unsaved };
}

/**
 * The one sentence the Data step, the Evaluations step and the create
 * handler's 400 all say. `null` when nothing selected is unsaved.
 *   none saved  `None of the 6 selected rows are in v6. Save them as a new version first.`
 *   some saved  `2 of the 8 selected rows aren't in v6 yet. Save them as a new version first, or they are skipped.`
 */
export function unsavedSentence({ total, unsaved } = {}, versionNumber) {
  if (!isSurfaceShown("dataset.versions") || !unsaved) return null;
  const v = `v${versionNumber ?? ""}`;
  if (unsaved >= total)
    return `None of the ${rowsWord(total)} ${total === 1 ? "is" : "are"} in ${v}. Save ${total === 1 ? "it" : "them"} as a new version first.`;
  return `${unsaved} of the ${rowsWord(total)} ${unsaved === 1 ? "isn't" : "aren't"} in ${v} yet. Save ${unsaved === 1 ? "it" : "them"} as a new version first, or ${unsaved === 1 ? "it is" : "they are"} skipped.`;
}

/**
 * The split every door row is headed for (`Put the new rows in a split` in
 * the import sheet), or null when the rows go to several or none. The Data
 * step preselects it so the re-test stays re-runnable after the toast.
 */
export function doorSplitOf(ops, rowIds) {
  const ids = (rowIds ?? []).filter(Boolean);
  if (!ids.length) return null;
  const byId = new Map(pendingAdds(ops).map((o) => [o.temp_id, o]));
  let split = null;
  for (const id of ids) {
    const s = byId.get(id)?.split_ids ?? [];
    if (s.length !== 1) return null;
    if (split && s[0] !== split) return null;
    split = s[0];
  }
  return split;
}

/** Pending rows headed for each split: `{[splitId]: n}`. */
export function pendingBySplit(ops) {
  const out = {};
  pendingAdds(ops).forEach((o) =>
    (o.split_ids ?? []).forEach((s) => {
      out[s] = (out[s] ?? 0) + 1;
    }),
  );
  return out;
}

/** The ids after a save: `row_id_map` turns temp ids into saved ids. */
export const remapRowIds = (ids, map) =>
  (ids ?? []).map((id) => map?.[id] ?? id);

/**
 * L5 G3: the row ids a door opens the wizard on after the pending-changes
 * gate: `saved` (the save result) follows unsaved rows into the version
 * through its `row_id_map`; without a save (`Use vN without them`) the
 * unsaved rows are dropped. Pure.
 * @param {{id: string, unsaved: boolean}[]} rows
 * @param {{row_id_map?: object}|undefined} saved
 */
export function rowIdsAfterGate(rows, saved) {
  const map = saved?.row_id_map ?? null;
  // W3 (L4 B08b): a selected row whose temp id the save mapped follows its
  // saved id even when the grid node did not flag it as pending.
  return (rows ?? [])
    .map((r) => map?.[r.id] ?? (r.unsaved ? null : r.id))
    .filter(Boolean);
}
