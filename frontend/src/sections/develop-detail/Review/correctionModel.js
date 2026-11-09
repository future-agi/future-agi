// Review corrections (W4-SCOPE §8 C1; MUST-BUILD W4-3): pure helpers for the
// Corrections block and the Correct field. A correction is the server's
// ReviewCorrection (MOCK/CONTRACT.md "W4"). No React, no I/O.

/** Reviewer names in first-seen order, deduplicated (`Meera Iyer, Asha Rao`). */
export function reviewerNames(corrections) {
  const out = [];
  (corrections ?? []).forEach((c) => {
    const name = c?.reviewer?.name ?? c?.reviewer?.email;
    if (name && !out.includes(name)) out.push(name);
  });
  return out;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * The N2 version note: `12 corrections from review “Quality · #4A · Oct 12”
 * by Meera Iyer, Asha Rao`; several queues read `from 2 reviews`.
 */
export function versionNote(corrections) {
  const list = corrections ?? [];
  const queues = [
    ...new Set(list.map((c) => c?.source?.queue_name).filter(Boolean)),
  ];
  const from =
    queues.length === 1
      ? ` from review “${queues[0]}”`
      : queues.length > 1
        ? ` from ${queues.length} reviews`
        : "";
  const names = reviewerNames(list);
  return `${plural(list.length, "correction")}${from}${names.length ? ` by ${names.join(", ")}` : ""}`;
}

/** `Save 12 corrections as v7` (or `Save 1 correction as v7`). */
export const saveLabel = (n, nextVersion) =>
  `Save ${plural(n, "correction")} as v${nextVersion}`;

/** The N2 `update_cell` operations of the accepted corrections (last one per cell wins). */
export function correctionOps(corrections) {
  const byCell = new Map();
  (corrections ?? []).forEach((c) =>
    byCell.set(`${c.row_id}|${c.column_id}`, c),
  );
  return [...byCell.values()].map((c) => ({
    op: "update_cell",
    row_id: c.row_id,
    column_id: c.column_id,
    value: c.after,
  }));
}

/** Corrections that would write the same cell twice (they need one pick). */
export function cellConflicts(corrections) {
  const seen = new Map();
  (corrections ?? []).forEach((c) => {
    const key = `${c.row_id}|${c.column_id}`;
    seen.set(key, [...(seen.get(key) ?? []), c.id]);
  });
  return [...seen.values()].filter((ids) => ids.length > 1);
}

/** `Row 33 · expected_reply · by Meera Iyer` (outside reviewers say so). */
export function correctionTitle(c) {
  const who = c?.reviewer?.name ?? c?.reviewer?.email ?? "a reviewer";
  const outside = c?.reviewer?.outside ? " (outside reviewer)" : "";
  return [c?.row_label, c?.column_name, `by ${who}${outside}`]
    .filter(Boolean)
    .join(" · ");
}

/** Status words of one correction. */
export function statusWords(c) {
  if (c?.status === "applied") return `Saved in v${c.applied_version}`;
  if (c?.status === "dismissed") return "Dismissed";
  if (c?.in_dataset) return "Already in the dataset";
  if (c?.stale) return "The row changed since this was suggested";
  return "Suggested";
}

/**
 * The suggestions to accept by default: every suggested one that is not
 * stale or already in the dataset, the newest per cell.
 */
export function defaultAccepted(corrections) {
  const byCell = new Map();
  (corrections ?? [])
    .filter((c) => c.status === "suggested" && !c.stale && !c.in_dataset)
    .forEach((c) => {
      const key = `${c.row_id}|${c.column_id}`;
      const prior = byCell.get(key);
      if (!prior || String(c.updated_at) > String(prior.updated_at))
        byCell.set(key, c);
    });
  return new Set([...byCell.values()].map((c) => c.id));
}

/** True when the editor's text differs from the current value. */
export const changed = (current, draft) =>
  String(draft ?? "") !== String(current ?? "") &&
  String(draft ?? "").trim().length > 0;

/** True when a mutation event is this item's successful annotation submit. */
export const isSubmitOf = (event, ctx) => {
  if (event?.type !== "updated" || event.action?.type !== "success")
    return false;
  const v = event.mutation?.state?.variables;
  return (
    Boolean(v) &&
    Array.isArray(v.annotations) &&
    v.queueId === ctx?.queue_id &&
    v.itemId === ctx?.item_id
  );
};

/**
 * The `source` a correction's pending N2 op carries (W4 §9, M6): N2 keeps it
 * on the revision, and the version's `contributors` read its reviewer.
 */
export function correctionSource(c) {
  return {
    kind: "review_correction",
    correction_id: c?.id ?? null,
    reviewer: c?.reviewer?.name
      ? {
          id: c.reviewer.id ?? null,
          name: c.reviewer.name,
          outside: Boolean(c.reviewer.outside),
        }
      : null,
    queue_name: c?.source?.queue_name ?? null,
  };
}
