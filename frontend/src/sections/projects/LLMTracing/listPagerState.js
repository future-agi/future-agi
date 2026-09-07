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
 */
export const windowedPageNumbers = ({ page, provenNext = false } = {}) => {
  const current = Number.isSafeInteger(page) && page >= 1 ? page : 1;
  const highest = current + (provenNext ? 1 : 0);
  const pages = new Set([1]);
  for (let candidate = current - 1; candidate <= highest; candidate += 1) {
    if (candidate >= 1) pages.add(candidate);
  }
  return Array.from(pages).sort((left, right) => left - right);
};
