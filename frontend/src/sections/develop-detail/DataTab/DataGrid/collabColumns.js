// HO D4 (W4 §9 + W5 C1 integration): the Data grid's optional Row info
// columns Comments, Assigned to and Tags. Off by default; View › Columns
// shows them. Comments and Assigned to are offered where the W4 reads are
// served; Tags only when some row carries tags (W5 C1). Every value comes
// from a read: the dataset's comment summary, its open assignments and the
// row's own `metadata.tags`. Pure.

export const COLLAB_COLUMN = Object.freeze({
  comments: "system:comments",
  assigned: "system:assigned",
  tags: "system:tags",
});

/** Catalog entries (useDataGridLayout) for the columns offered. */
export function collabCatalogEntries({ served = false, hasTags = false } = {}) {
  const entry = (id, label) => ({
    id,
    label,
    group: "system",
    defaultVisible: false,
    pinnable: false,
  });
  return [
    ...(served
      ? [
          entry(COLLAB_COLUMN.comments, "Comments"),
          entry(COLLAB_COLUMN.assigned, "Assigned to"),
        ]
      : []),
    ...(hasTags ? [entry(COLLAB_COLUMN.tags, "Tags")] : []),
  ];
}

/** Some row carries a tag (get-dataset-table `metadata.counts.tags`). */
export const datasetHasTags = (tableMeta) =>
  Object.values(tableMeta?.counts?.tags ?? {}).some((n) => Number(n) > 0);

/** `{row_id: {open, resolved, total}}` from the dataset comment summary. */
export function commentsByRow(summary) {
  const out = {};
  (summary?.rows ?? []).forEach((r) => {
    if (r?.row_id)
      out[r.row_id] = {
        open: r.open ?? 0,
        resolved: r.resolved ?? 0,
        total: r.total ?? 0,
      };
  });
  return out;
}

/** `2 open · 1 resolved`, `1 resolved`, `` (no thread). */
export function commentsCellText(c) {
  if (!c?.total) return "";
  const parts = [];
  if (c.open) parts.push(`${c.open} open`);
  if (c.resolved) parts.push(`${c.resolved} resolved`);
  return parts.join(" · ");
}

/** `{row_id: [names]}` of the open assignments (a row once per person). */
export function assigneesByRow(read) {
  const out = {};
  (read?.assignments ?? []).forEach((a) => {
    if (!a?.row_id || a.run_id || (a.status && a.status !== "open")) return;
    const name = a.assignee?.name ?? a.assignee_name ?? null;
    if (!name) return;
    out[a.row_id] = [...new Set([...(out[a.row_id] ?? []), name])];
  });
  return out;
}

/** A grid row's tags (`row_meta.metadata.tags`). */
export const rowTagNames = (row) => {
  const t = row?.row_meta?.metadata?.tags ?? row?.metadata?.tags;
  return Array.isArray(t) ? t.filter(Boolean).map(String) : [];
};

/**
 * Registry records for tag names (registry colours); a name the registry
 * does not list yet shows with no colour token.
 */
export function tagRecordsFor(names, registry) {
  const byName = new Map(
    (registry ?? []).map((t) => [String(t.name).toLowerCase(), t]),
  );
  return (names ?? []).map(
    (n) => byName.get(n.toLowerCase()) ?? { id: `name:${n}`, name: n },
  );
}
