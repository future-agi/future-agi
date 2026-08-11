import { followEmptyListContinuations } from "./listCursorPagination";

// `limit_reached` describes one bounded backend walk, not necessarily the end
// of the retained catalog. When the response also carries an advancing signed
// cursor the next explicit Load more action must be able to continue. Only
// `exhausted` is an unconditional terminal browse state.
const TERMINAL_BROWSE_STATUSES = new Set(["exhausted"]);
const FOLLOWED_CURSORS_KEY = "__attributeKeyFollowedCursors";
const CURSOR_STOPPED_KEY = "__attributeKeyCursorStopped";

// The shared Axios client intentionally has no global timeout. Attribute-key
// browsing is interactive, so one stalled proxy/backend response must not
// leave a picker in an endless loading state. Keep this just above the
// server-side 30-second ceiling so structured server timeouts still win.
export const ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS = 35_000;

const attributeKey = (item) =>
  typeof item?.key === "string" && item.key.length > 0 ? item.key : null;

const normalizeAttributeKeyPage = (page = {}) =>
  TERMINAL_BROWSE_STATUSES.has(page?.browse_status)
    ? { ...page, has_more: false, next_cursor: null }
    : page;

const stopAttributeKeyCursor = (page, reason) => ({
  ...page,
  [CURSOR_STOPPED_KEY]: reason,
});

export const isAttributeKeyCursorStopped = (page) =>
  typeof page?.[CURSOR_STOPPED_KEY] === "string";

export const getAttributeKeyNextCursor = (page) => {
  if (isAttributeKeyCursorStopped(page)) return undefined;
  const normalized = normalizeAttributeKeyPage(page);
  const cursor = normalized?.next_cursor;
  return normalized?.has_more === true &&
    typeof cursor === "string" &&
    cursor.length > 0
    ? cursor
    : undefined;
};

/**
 * Read one visible attribute-key page.
 *
 * ClickHouse can advance a signed cursor after proving that a bounded physical
 * slice contains no new keys. Such a response is a transport checkpoint, not
 * an empty picker page. Follow advancing checkpoints until a new key arrives
 * or the server proves exhaustion. The shared follower bounds one browser
 * action. If that bound is reached, return the still-advancing checkpoint to
 * the picker so the user can continue with another bounded Load more action;
 * never start an unbounded background request chain.
 */
export const readAttributeKeyPage = async ({
  pageParam,
  requestPage,
  signal,
}) => {
  // De-duplicate only inside this bounded transport chunk. React Query
  // refetches rebuild their output while the old cache is still readable, so
  // consulting cached keys here would erase unchanged results. Hook consumers
  // de-duplicate the published pages, while getNextAttributeKeyPageParam
  // validates cursors across chunks.
  const knownKeys = new Set();
  const knownCursors = new Set(
    typeof pageParam === "string" && pageParam.length > 0 ? [pageParam] : [],
  );
  const followedCursors = new Set();
  const uniqueRowsByPage = new WeakMap();

  const uniqueRows = (page) => {
    if (page && typeof page === "object" && uniqueRowsByPage.has(page)) {
      return uniqueRowsByPage.get(page);
    }
    const rows = (Array.isArray(page?.result) ? page.result : []).filter(
      (item) => {
        const key = attributeKey(item);
        if (!key || knownKeys.has(key)) return false;
        knownKeys.add(key);
        return true;
      },
    );
    if (page && typeof page === "object") uniqueRowsByPage.set(page, rows);
    return rows;
  };

  const checkedMetadata = (page) => {
    const normalized = normalizeAttributeKeyPage(page);
    if (normalized?.has_more !== true) return normalized;
    const nextCursor = normalized?.next_cursor;
    if (typeof nextCursor !== "string" || nextCursor.length === 0) {
      return stopAttributeKeyCursor(normalized, "malformed_cursor");
    }
    if (knownCursors.has(nextCursor) || followedCursors.has(nextCursor)) {
      return stopAttributeKeyCursor(normalized, "repeated_cursor");
    }
    return normalized;
  };

  // The private marker is the client-side retry contract. Give the shared
  // transport follower a terminal projection so it stops without mutating or
  // impersonating the API response fields published to React Query.
  const continuationMetadata = (page) => {
    const checked = checkedMetadata(page);
    return isAttributeKeyCursorStopped(checked)
      ? { ...checked, has_more: false, next_cursor: null }
      : checked;
  };

  const initialPage = await requestPage(pageParam);
  const page = await followEmptyListContinuations({
    initialResponse: initialPage,
    rowsFromResponse: uniqueRows,
    metadataFromResponse: continuationMetadata,
    nextResponse: requestPage,
    onContinuation: (metadata) => {
      const nextCursor = getAttributeKeyNextCursor(metadata);
      if (nextCursor) {
        followedCursors.add(nextCursor);
        knownCursors.add(nextCursor);
      }
    },
    isCurrent: () => !signal?.aborted,
  });
  const normalized = checkedMetadata(page);
  const visibleRows = uniqueRows(page);

  return {
    ...normalized,
    // Transport-only and duplicate-only rows are never published to picker
    // consumers. If this bounded action stopped at an advancing checkpoint,
    // next_cursor remains available for the next explicit Load more action.
    result: visibleRows,
    // Store only cursors consumed by this chunk. Copying the cumulative cursor
    // history onto every page makes long sparse catalogs grow quadratically.
    [FOLLOWED_CURSORS_KEY]: [...followedCursors],
  };
};

export const getNextAttributeKeyPageParam = (
  lastPage,
  allPages,
  lastPageParam,
  allPageParams,
) => {
  const nextCursor = getAttributeKeyNextCursor(lastPage);
  if (!nextCursor) return undefined;

  const consumedCursors = new Set(
    (allPageParams || []).filter(
      (cursor) => typeof cursor === "string" && cursor.length > 0,
    ),
  );
  for (const page of allPages || []) {
    for (const cursor of page?.[FOLLOWED_CURSORS_KEY] || []) {
      consumedCursors.add(cursor);
    }
  }

  return nextCursor === lastPageParam || consumedCursors.has(nextCursor)
    ? undefined
    : nextCursor;
};

/**
 * Detect a cursor protocol failure across already-published React Query pages.
 *
 * A bounded chunk can validate its own cursor hops without consulting cached
 * rows. A later chunk can still return a cursor consumed by an older chunk,
 * though. React Query correctly refuses to fetch that cursor, but an undefined
 * next-page parameter would otherwise look identical to real exhaustion. Keep
 * that state explicitly degraded and retryable instead.
 */
export const isAttributeKeyCursorChainStopped = (data) => {
  const pages = Array.isArray(data?.pages) ? data.pages : [];
  if (pages.some(isAttributeKeyCursorStopped)) return true;
  if (pages.length === 0) return false;

  const pageParams = Array.isArray(data?.pageParams) ? data.pageParams : [];
  const lastPage = pages.at(-1);
  const nextCursor = getAttributeKeyNextCursor(lastPage);
  if (!nextCursor) return false;

  const lastPageParam = pageParams.at(-1);
  return (
    getNextAttributeKeyPageParam(lastPage, pages, lastPageParam, pageParams) ===
    undefined
  );
};

/**
 * Stable identity for one deterministic cursor-protocol stop.
 *
 * Consumers use this to offer one explicit fresh-chain retry without turning a
 * malformed/repeated cursor into an endless Retry loop. If a later request
 * advances to a different physical cursor, it is a new stop and may be retried
 * independently.
 */
export const getAttributeKeyCursorStopSignature = (data) => {
  if (!isAttributeKeyCursorChainStopped(data)) return null;

  const pages = Array.isArray(data?.pages) ? data.pages : [];
  const pageParams = Array.isArray(data?.pageParams) ? data.pageParams : [];
  const lastPage = pages.at(-1) || {};
  const lastPageParam = pageParams.at(-1);

  return JSON.stringify([
    lastPage?.[CURSOR_STOPPED_KEY] || "chain_stopped",
    typeof lastPageParam === "string" ? lastPageParam : null,
    typeof lastPage?.next_cursor === "string" ? lastPage.next_cursor : null,
  ]);
};
