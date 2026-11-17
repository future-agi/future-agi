// W4 §9 (M6, the W1 A run / edit / download gates): what the Experiments
// tab lets the caller do, from the role and the dataset's access view
// (useDatasetAccess). Pure. The guard's classes: pin baseline, rename and
// duplicate are edit; rerun, stop, resume and delete runs are run; the CSV
// is download. While the access view loads, the four stay allowed (the
// server still refuses) and the tips keep their words.

export const NO_EDIT = "You need edit access to this dataset";
export const NO_DELETE = "You need delete access to this dataset";

/**
 * @param {{roleEdit: boolean, roleDelete: boolean}} role
 * @param {{can: object, reason: (perm) => string|null}|null} access
 */
export function leaderboardGates({ roleEdit, roleDelete }, access) {
  const can = access?.can ?? {};
  const has = (perm) => can[perm] !== false;
  const why = (perm, fallback) =>
    (!has(perm) && access?.reason?.(perm)) || fallback;
  return {
    canEdit: Boolean(roleEdit) && has("edit"),
    canRun: Boolean(roleEdit) && has("run"),
    canDelete: Boolean(roleDelete) && has("run"),
    canDownload: has("download"),
    tips: {
      edit: roleEdit ? why("edit", NO_EDIT) : NO_EDIT,
      run: roleEdit ? why("run", NO_EDIT) : NO_EDIT,
      delete: roleDelete ? why("run", NO_DELETE) : NO_DELETE,
      download: why("download", null),
    },
  };
}

/** The tip for a refused door, from the tab's context (or the old words). */
export const editTip = (ctx) => ctx?.tips?.edit ?? NO_EDIT;
export const runTip = (ctx) => ctx?.tips?.run ?? NO_EDIT;
export const deleteTip = (ctx) => ctx?.tips?.delete ?? NO_DELETE;
/** A run-class door: `canRun` when the context has it, else edit. */
export const canRunOf = (ctx) => ctx?.canRun ?? Boolean(ctx?.canEdit);
