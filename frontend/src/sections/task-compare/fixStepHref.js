// IT-R2 G4 P13 (seam S1 → G3): the `Fix this step` / `Test a fix` link from
// Task compare. Opens the Iterate dock on the lane's experiment scoped to
// the rows that fail for the same cause (`scope=rows`, `rows=` every row of
// the R5 root cause that holds this row, `cause=` its id), with the pair
// (`cand` = the lane, `baseline`) and `back` (this compare URL, encoded) so
// the dock can say `← Back to the trace`. Without a resolved cause the
// scope is this row alone. Pure.

/**
 * The cause of `lane` that covers `rowId` (R5 `root_causes` item) or null.
 * @param {object[]} causes  R5 root causes ({id, run_id, rows:[{row_id}]})
 */
export function causeFor(causes, laneId, rowId) {
  const list = (causes ?? []).filter((c) => c?.run_id === laneId);
  return (
    list.find((c) => (c.rows ?? []).some((r) => r?.row_id === rowId)) ?? null
  );
}

/**
 * @param {{lane: {id: string, experiment_id: string}, node?: string,
 *   rowId?: string, causes?: object[], baselineId?: string|null,
 *   back?: string, iterateHref: Function}} a
 * @returns {string}
 */
/** `baseline=` of a same-app URL (the compare page `back` points to), or null. */
export function pinnedBaselineOf(back) {
  if (!back) return null;
  const q = String(back).split("?")[1];
  if (!q) return null;
  try {
    return new URLSearchParams(q.split("#")[0]).get("baseline") || null;
  } catch {
    return null;
  }
}

/**
 * iterate r3 G3 (S2, P3): the subject never flips and the page never
 * re-bases. `cand` = the lane being fixed (the subject), `baseline` = the
 * compare page's pinned baseline (read from `back`; else `baselineId`),
 * `compared` = the lane it was compared with when that is not the baseline
 * (the experiment page shows it as a chip `compared with #2A`, never as a
 * candidate). Callers keep today's arguments: `baselineId` is "the pair's
 * other run" (the baseline, or the run the baseline was compared with).
 */
export function fixStepHref({
  lane,
  node,
  rowId,
  causes,
  baselineId,
  back,
  iterateHref,
  pinnedBaselineId,
  inPlace = true,
}) {
  const cause = causeFor(causes, lane.id, rowId);
  const causeRows = (cause?.rows ?? []).map((r) => r.row_id).filter(Boolean);
  const rows = causeRows.length ? causeRows : rowId ? [rowId] : [];
  // IT-R5 G3 (P4): from the side by side page the panel opens ON that
  // page (the open row and step stay put): the same URL plus the dock's
  // one-shot params. The scope is the cause's rows.
  const backPath = String(back ?? "").split("?")[0];
  if (inPlace && /\/compare\/tasks\/?$/.test(backPath)) {
    // L3 G9 (fix 9): the cause's rows with its fix drafted (change = the
    // cause id, as the root-cause card opens it); the lane's failed rows
    // without a cause.
    const here = iterateHref(back, {
      runId: lane.id,
      scope: causeRows.length ? "rows" : "failed",
      rows: causeRows.length ? causeRows : undefined,
      change: cause?.id ?? undefined,
      cause: cause?.id ?? undefined,
    });
    const tail = [
      ["iterate_from", "failed_lane"],
      ["node", node],
    ]
      .filter(([, v]) => v)
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
      .join("&");
    return tail ? `${here}&${tail}` : here;
  }
  const base = iterateHref(
    `/dashboard/develop/experiment/${lane.experiment_id}/data`,
    {
      runId: lane.id,
      scope: rows.length ? "rows" : "failed",
      rows,
      cause: cause?.id ?? undefined,
    },
  );
  const pinned =
    pinnedBaselineId ?? pinnedBaselineOf(back) ?? baselineId ?? null;
  const compared =
    baselineId && baselineId !== lane.id && baselineId !== pinned
      ? baselineId
      : null;
  const extra = [
    ["iterate_from", "failed_lane"],
    ["node", node],
    ["cand", lane.id],
    ["baseline", pinned],
    ["compared", compared],
    ["back", back],
  ]
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join("&");
  return extra ? `${base}&${extra}` : base;
}
