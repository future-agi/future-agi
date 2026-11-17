// Drag datasets onto a folder (W9-SCOPE §5 A6): the plan behind the drag
// handle in the Name cell and the drop targets (rail nodes, breadcrumb
// segments). Dragging a selected row drags the whole selection; dropping
// moves only the datasets not already there. Pure apart from `save`.
import { saveInSequence, sequenceToast } from "src/api/develop/dataset-org";
import { moveLabel, planMove } from "./MoveToFolderDialog";
import { normalizeFolder } from "./orgRules";

/** The drop target id of a folder path ("" = out of any folder). */
export const dropId = (path) => `folder-drop:${normalizeFolder(path ?? "")}`;

/** The folder path of a drop target id, or null for anything else. */
export function pathOfDropId(id) {
  const s = String(id ?? "");
  return s.startsWith("folder-drop:") ? s.slice("folder-drop:".length) : null;
}

/**
 * The datasets a drag carries: the selection when the dragged row is in
 * it, else that row alone.
 * @param {object} row  the dragged dataset ({id, name, folder})
 * @param {object[]} selected
 */
export function draggedDatasets(row, selected) {
  if (!row) return [];
  const sel = (selected ?? []).filter(Boolean);
  return sel.some((d) => d.id === row.id) ? sel : [row];
}

/**
 * What a drop does: who moves, their previous folders (for Undo), and the
 * label shown while over the target (`Move 2 datasets to support`). Null
 * when nothing would change.
 */
export function dropPlan(datasets, path) {
  const plan = planMove(datasets, path ?? "");
  if (!plan.moving.length) return null;
  return { ...plan, label: moveLabel(plan.moving.length, plan.target) };
}

/**
 * What a finished drag does (exported for tests): the plan for the folder
 * it ended on, the writes in sequence, and the toast with Undo. Returns the
 * toast, or null when the drag changes nothing.
 */
export async function finishDrag({
  row,
  overId,
  selected,
  save = saveInSequence,
}) {
  const path = pathOfDropId(overId);
  if (path === null) return null;
  const plan = dropPlan(draggedDatasets(row, selected), path);
  if (!plan) return null;
  const result = await save(plan.moving, () => ({ folder: plan.target }));
  const toast = sequenceToast(
    result,
    "Moved",
    plan.target ? ` to ${plan.target}` : " out of any folder",
  );
  if (!result.saved.length) return toast;
  const back = result.saved.map((d) => ({ ...d, to: plan.previous[d.id] }));
  return {
    ...toast,
    undo: () => save(back, (d) => ({ folder: d.to })),
  };
}
