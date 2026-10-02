/**
 * Page-level state for the Error Feed list (TH-8209).
 *
 * The table used to read only the list query's `isLoading` and defaulted a
 * missing result to `[]`, so a failed read and a workspace with no Observe
 * projects both rendered the "No errors — everything looks good!" copy. The
 * page now composes the Observe catalog query and the feed list query into
 * one state, in this precedence:
 *
 *   1. error        — either read failed, or the feed answered without the
 *                     list contract ({data: [], total: n}). Nothing
 *                     reassuring is shown.
 *   2. loading      — either read has not succeeded yet.
 *   3. feed         — the feed has rows or a positive total (rows win over
 *                     an empty picker, which can be narrower than the feed).
 *   4. no-projects  — the catalog is empty AND the feed succeeded with
 *                     total 0 / no rows. Only then is absence of projects
 *                     the explanation.
 *   5. feed         — otherwise the ordinary (possibly filtered) empty table.
 *
 * `catalog` / `feed` are React Query results (isError, isSuccess, data).
 */
export const FEED_PAGE_STATE = Object.freeze({
  LOADING: "loading",
  ERROR: "error",
  NO_PROJECTS: "no-projects",
  FEED: "feed",
});

export function deriveFeedPageState({ catalog, feed }) {
  if (feed.isError || catalog.isError) return FEED_PAGE_STATE.ERROR;
  if (!feed.isSuccess || !catalog.isSuccess) return FEED_PAGE_STATE.LOADING;

  const list = feed.data;
  const rows = Array.isArray(list?.data) ? list.data : null;
  const total = typeof list?.total === "number" ? list.total : null;
  // Strict response validation is off by default in the axios layer, so a
  // 200 without the list contract would otherwise read as "zero errors".
  if (rows === null || total === null) return FEED_PAGE_STATE.ERROR;
  if (rows.length > 0 || total > 0) return FEED_PAGE_STATE.FEED;

  const projects = catalog.data;
  if (Array.isArray(projects) && projects.length === 0) {
    return FEED_PAGE_STATE.NO_PROJECTS;
  }
  return FEED_PAGE_STATE.FEED;
}
