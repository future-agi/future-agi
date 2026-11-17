// The pair the fix-run banner is about (iterate r2 G3 P2). Pure. One rule
// for the banner and for the results verdict, which hides its own line when
// the banner already speaks about the same pair (one verdict on screen).
import { pageOf } from "./pageOf";

/**
 * @param {{pathname: string, search: string, datasetId?: string,
 *   runIds?: string[], baselineId?: string|null, last?: object|null,
 *   dismissed?: string[]}} args  `last` = the store's `lastFinished`
 * @returns {{baseId: string, candId: string, fromPage: boolean, last:
 *   object|null}|null}  null when no banner shows on this page
 */
export function bannerPairOf({
  pathname,
  search,
  datasetId,
  runIds,
  baselineId,
  last,
  dismissed,
}) {
  const ids = runIds ?? [];
  const sp = new URLSearchParams(search ?? "");
  if (/\/summary\/?$/.test(pathname ?? "")) return null;
  const pageBase = baselineId ?? ids[0] ?? null;
  const pageCand = ids.find((id) => id !== pageBase) ?? null;
  const fromPage = sp.get("from") === "iterate" && pageBase && pageCand;
  // The fix-run that just finished shows on the page it started from, or
  // on any page with both runs; the run never navigates on its own.
  const lastHere =
    last &&
    last.datasetId === datasetId &&
    last.baseId &&
    (pageOf(pathname) === pageOf(last.startedPath) ||
      (ids.includes(last.baseId) && ids.includes(last.runId)))
      ? last
      : null;
  const baseId = fromPage ? pageBase : lastHere?.baseId ?? null;
  const candId = fromPage ? pageCand : lastHere?.runId ?? null;
  if (!baseId || !candId) return null;
  if ((dismissed ?? []).includes(candId)) return null;
  return { baseId, candId, fromPage: Boolean(fromPage), last: lastHere };
}
