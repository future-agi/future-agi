// The run a row opens on (iterate r3 G3 P1). Pure.
//
// Opened from a filtered list, the drawer focuses the run being triaged:
//   a pill about one run (`#7B Task success worse`) › on a Worse than / Better
//   than list the page's candidate when it moved that way on this row, else
//   the first run that did › the page's candidate › the first other run.
import {
  ANY_CANDIDATE,
  ANY_RUN,
  PILL_TYPES,
} from "src/sections/common/Results/filterPills";

/** Eval metrics only (`eval:*`): latency / cost / human moves are not a regression of the answer. */
export const isEvalKey = (key) => String(key).startsWith("eval:");

/** True when an eval of run `id` moved `dir` ("regressed" / "improved") on `row`. */
export const movedOn = (row, id, dir) =>
  Object.entries(row?.results?.[id]?.movement ?? {}).some(
    ([k, m]) => m === dir && isEvalKey(k),
  );
const moved = movedOn;

/**
 * `"regressed"` / `"improved"` when the list is Worse than / Better than the
 * baseline (Show or a movement pill), `"fails"` for a fails pill, else null.
 */
export function listDirection({ show, pills } = {}) {
  if (show === "regressed" || show === "improved") return show;
  const move = (pills ?? []).find(
    (p) =>
      p?.type === "movement" && (p.dir === "regressed" || p.dir === "improved"),
  );
  if (move) return move.dir;
  if ((pills ?? []).some((p) => p?.type === "fails")) return "fails";
  return null;
}

/** The one run a run-scoped pill names (not the baseline, not "any"), or null. */
export function pillRunOf(pills, { baselineId, runIds } = {}) {
  const ids = runIds ?? [];
  const hit = (pills ?? []).find(
    (p) =>
      PILL_TYPES[p?.type]?.runScoped &&
      p.run &&
      p.run !== ANY_CANDIDATE &&
      p.run !== ANY_RUN &&
      p.run !== baselineId &&
      ids.includes(p.run),
  );
  return hit?.run ?? null;
}

/**
 * @param {{row: object, pills?: object[], show?: string, baselineId?: string,
 *   runIds: string[], pageCand?: string|null}} args  `runIds` = the live runs
 * @returns {string|null}
 */
export function triageRunOf({
  row,
  pills,
  show,
  baselineId,
  runIds,
  pageCand,
}) {
  const ids = (runIds ?? []).filter((id) => id && id !== baselineId);
  const pinned = pillRunOf(pills, { baselineId, runIds });
  if (pinned) return pinned;
  const order =
    pageCand && ids.includes(pageCand)
      ? [pageCand, ...ids.filter((id) => id !== pageCand)]
      : ids;
  const dir = listDirection({ show, pills });
  if (dir === "regressed" || dir === "improved") {
    const hit = order.find((id) => moved(row, id, dir));
    if (hit) return hit;
  }
  return order[0] ?? baselineId ?? null;
}
