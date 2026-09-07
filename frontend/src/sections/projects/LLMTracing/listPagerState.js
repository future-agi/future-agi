/**
 * Cursor list endpoints never count matching rows. They publish a proven lower
 * bound, so a page count divided out of it is always `currentPage + 1`. These
 * helpers derive what the pager may honestly draw instead.
 *
 * Field names differ per endpoint: `total_rows` (traces/spans/sessions),
 * `total_count` (users), `count` (voice calls).
 */

const reportedTotal = (metadata) => {
  const raw =
    metadata?.total_rows ?? metadata?.total_count ?? metadata?.count ?? null;
  if (raw === null) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? Math.floor(value) : null;
};

const isLowerBound = (metadata) =>
  metadata?.total_rows_is_lower_bound === true ||
  metadata?.count_is_lower_bound === true ||
  metadata?.total_count_is_lower_bound === true;

/**
 * `has_more` is the cursor contract's marker. Its absence means the response
 * did not come from a cursor endpoint at all — the agent-definition call-log
 * list is a plain DRF PageNumberPagination view whose `count` is the
 * paginator's exact total (futureagi/tfc/utils/pagination.py).
 */
const hasCursorContract = (metadata) =>
  metadata != null &&
  typeof metadata === "object" &&
  Object.prototype.hasOwnProperty.call(metadata, "has_more");

/**
 * `has_more` means "more window left to search", NOT "another row exists"
 * (see list_cursor.py:518-520). Only a reported total strictly greater than
 * the rows already seen proves a further page has content, so only then may a
 * page number be drawn for it.
 */
export const getListPagerState = ({
  metadata = null,
  startRow = 0,
  rowCount = 0,
} = {}) => {
  const start = Number.isFinite(Number(startRow)) ? Number(startRow) : 0;
  const rows = Number.isFinite(Number(rowCount)) ? Number(rowCount) : 0;
  // On the sparse cursor path a page can publish fewer rows than the page
  // size, so the true cumulative count can be lower than this. That only ever
  // under-reports `provenNext`; it can never inflate it.
  const seen = Math.max(0, start + rows);
  const total = reportedTotal(metadata);
  const exactTotal = total !== null && !isLowerBound(metadata) ? total : null;

  // A non-cursor list reports a real total, so a further page is proven
  // whenever that total exceeds the rows seen. The reasoning that forbids
  // counting pages out of a cursor lower bound does not apply here.
  if (!hasCursorContract(metadata)) {
    const provenNext = exactTotal !== null && exactTotal > seen;
    return { hasMore: provenNext, seen, provenNext, exactTotal };
  }

  const hasMore = metadata.has_more === true;
  return {
    hasMore,
    seen,
    provenNext: hasMore && total !== null && total > seen,
    exactTotal,
  };
};

/**
 * `{1} ∪ {cur-1, cur, cur+1}`, clipped to the furthest proven page — which is
 * exactly the set the cursor protocol can serve. Cursors exist only for page 1
 * (needs none), pages already visited, and `pageNumber + 1`
 * (listCursorPagination.js:618); anything else throws.
 *
 * `furthestPage` adds a fifth, right-hand boundary number: the deepest page
 * this pagination generation has ever reached (the monotone "frontier"),
 * drawn even when the walk has since returned to an earlier page. It is only
 * ever added when it sits strictly beyond the window computed above and
 * strictly ahead of `page` — never behind or equal to the page on screen, and
 * never redundant with a number the window already contains. That keeps the
 * window at four numbers during a forward walk (the frontier is always
 * inside the window there) and caps it at five only once the walk has gone
 * back. Callers must gate `furthestPage` on reachability themselves
 * (`listCursorPagination.js`'s `canReachPage`) — this function draws whatever
 * number it is given.
 */
export const windowedPageNumbers = ({
  page,
  provenNext = false,
  furthestPage = 0,
} = {}) => {
  const current = Number.isSafeInteger(page) && page >= 1 ? page : 1;
  const highest = current + (provenNext ? 1 : 0);
  const pages = new Set([1]);
  for (let candidate = current - 1; candidate <= highest; candidate += 1) {
    if (candidate >= 1) pages.add(candidate);
  }
  const boundary = Number.isSafeInteger(furthestPage) ? furthestPage : 0;
  const rightEdge = Math.max(highest, boundary);
  if (rightEdge > current) pages.add(rightEdge);
  return Array.from(pages).sort((left, right) => left - right);
};

export const EMPTY_PAGER_FRONTIER = Object.freeze({
  page: 0,
  hasMore: false,
  provenNext: false,
});

/**
 * Derive the pager flags for the page currently on screen from the deepest
 * page the datasource has published. Every page below the frontier is already
 * known to be followed by a page that has been fetched; only at the frontier
 * itself does the last response get to decide.
 */
export const pagerFlagsForPage = (page, frontier = EMPTY_PAGER_FRONTIER) => ({
  hasMore: page < frontier.page || frontier.hasMore === true,
  provenNext: page < frontier.page + (frontier.provenNext ? 1 : 0),
});

/**
 * `isLastPage` can only be false while the transport reports no further search
 * window when the terminal response overflowed and its surplus rows are
 * already buffered for the next page (listCursorPagination.js, `completeVisiblePage`
 * / `loadExactListPage`). Those rows are proven to exist by construction —
 * a stronger proof than any reported count — so the next page must be
 * reachable even though nothing in the metadata says so.
 *
 * That proof itself rests on a precondition: `isLastPage` only degrades to
 * this "no further window but not terminal" shape in cursor mode, where
 * `isLastPage()` (listCursorPagination.js:805-810) reads `metadata.has_more`.
 * In legacy/numbered mode — no `has_more` field at all — `isLastPage` instead
 * collapses to `rowCount < pageSize`, and a full final page reads back false
 * with nothing buffered behind it. Gate on the field's presence (the same
 * `hasCursorContract` check above), not just its value, or a full last page
 * in numbered mode manufactures a page that does not exist.
 */
export const hasBufferedOverflowPage = (isLastPage, metadata) =>
  isLastPage === false &&
  hasCursorContract(metadata) &&
  metadata.has_more !== true;
