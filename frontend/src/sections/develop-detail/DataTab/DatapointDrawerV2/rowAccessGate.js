// W4 §9 (M6, the RowView integration): what the row view lets the caller do,
// from the role, the row's state and the dataset's access view
// (useDatasetAccess). Pure.
//
// - Edit, + Split and the ⋯ edit items need edit on the dataset; while the
//   access view loads (or a build has no access read) edit stays allowed and
//   the server's guard still refuses.
// - Annotate adds the row to a queue, which the guard classes as edit.
// - integration: as of an older version the row view is read-only everywhere
//   (no edit, no annotate: queue, comments, new experiment); `rowViewGate`
//   applies that on top (rowAccessGate's own shape is pinned by M6).
// - `editReason` is the server's sentence for a disabled Edit (tooltip).

/** {canEdit, annotate, editReason} for the row view. */
export function rowAccessGate({
  viewer = false,
  asOf = false,
  pendingDelete = false,
  isImported = false,
  access = null,
} = {}) {
  const allowed = access?.can?.edit !== false;
  const editReason =
    !viewer && !allowed ? access?.reason?.("edit") ?? null : null;
  return {
    canEdit: !viewer && allowed && !asOf && !pendingDelete && !isImported,
    annotate: !viewer && allowed,
    editReason,
  };
}

/**
 * integration · the Row view's gate: rowAccessGate, read-only as of an older
 * version (`annotate` off too), plus `asOf` itself. Pure.
 */
export function rowViewGate(opts = {}) {
  const gate = rowAccessGate(opts);
  const asOf = Boolean(opts.asOf);
  return { ...gate, annotate: gate.annotate && !asOf, asOf };
}

/**
 * The Activity tab's badge: N8's events and one per run, plus the row's
 * comment threads (N8 merges each thread into the feed, so the All chip
 * counts them).
 */
export function activityBadge(counts = {}, threads = []) {
  if (!Number.isFinite(counts.activity)) return 0;
  return counts.activity + (counts.runs ?? 0) + (threads?.length ?? 0);
}
