import { accumulateUniqueListContinuations } from "./listCursorPagination";
import { FILTER_VALUE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

// `limit_reached` describes one bounded backend walk, not necessarily the end
// of the retained catalog. When the response also carries an advancing signed
// cursor the next explicit Load more action must be able to continue. Only
// `exhausted` is an unconditional terminal browse state.
const TERMINAL_BROWSE_STATUSES = new Set(["exhausted"]);
const FOLLOWED_CURSORS_KEY = "__attributeKeyFollowedCursors";
const CURSOR_STOPPED_KEY = "__attributeKeyCursorStopped";

// The shared Axios client intentionally has no global timeout. Attribute-key
// browsing is interactive, so one stalled proxy/backend response must not
// leave a picker in an endless loading state. Its configured browser wall is
// independent from the server wall so proxy stalls also release the UI.
export const ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS = FILTER_VALUE_REQUEST_TIMEOUT_MS;

const attributeKey = (item) =>
  typeof item?.key === "string" && item.key.length > 0 ? item.key : null;

const workspaceAttributeKeyTypeIdentity = (item) => {
  const key = attributeKey(item);
  if (!key) return null;
  const types = [item?.type, ...(Array.isArray(item?.types) ? item.types : [])]
    .filter(
      (valueType, index, values) =>
        valueType && values.indexOf(valueType) === index,
    )
    .sort();
  return `${key}\0${types.join("\0")}`;
};

export const compactAttributeKeyRetryPage = (previousData, freshPage) => {
  const rowsByKey = new Map();
  const rows = [
    ...(previousData?.pages || []).flatMap((page) => page?.result || []),
    ...(freshPage?.result || []),
  ];

  for (const row of rows) {
    const key = attributeKey(row);
    if (!key) continue;
    const existing = rowsByKey.get(key);
    if (!existing) {
      rowsByKey.set(key, row);
      continue;
    }
    const types = [
      existing.type,
      ...(existing.types || []),
      row.type,
      ...(row.types || []),
    ].filter(
      (valueType, index, values) =>
        valueType && values.indexOf(valueType) === index,
    );
    rowsByKey.set(key, {
      ...existing,
      ...row,
      key,
      type: existing.type || row.type || types[0],
      types,
      // A refreshed first page cannot prove that type families observed on
      // older retained pages are exhaustive.
      types_exact: existing.types_exact === true && row.types_exact === true,
    });
  }

  return { ...freshPage, result: [...rowsByKey.values()] };
};

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
 * proof of exhaustion. Publish that checkpoint immediately so the next
 * explicit Load more gesture can advance its signed cursor. One browser
 * action performs exactly one physical request; it never starts a background
 * continuation chain that can exceed the interaction deadline.
 */
export const readAttributeKeyPage = async ({
  pageParam,
  pageSize = 10,
  publishedData,
  requestPage,
  signal,
  dedupeByType = false,
}) => {
  const actionStartedAt = Date.now();
  const isFreshChainRead = pageParam == null;
  const publishedPages = isFreshChainRead ? [] : publishedData?.pages || [];
  const rowIdentity = dedupeByType
    ? workspaceAttributeKeyTypeIdentity
    : attributeKey;
  const knownIdentities = publishedPages.flatMap((page) =>
    (Array.isArray(page?.result) ? page.result : [])
      .map(rowIdentity)
      .filter(Boolean),
  );
  const knownCursors = new Set(
    [
      ...(isFreshChainRead ? [] : publishedData?.pageParams || []),
      ...publishedPages.flatMap((page) => page?.[FOLLOWED_CURSORS_KEY] || []),
      pageParam,
    ].filter((cursor) => typeof cursor === "string" && cursor.length > 0),
  );

  const checkedMetadata = (page) => {
    const normalized = normalizeAttributeKeyPage(page);
    if (normalized?.has_more !== true) return normalized;
    const nextCursor = normalized?.next_cursor;
    if (typeof nextCursor !== "string" || nextCursor.length === 0) {
      return stopAttributeKeyCursor(normalized, "malformed_cursor");
    }
    if (knownCursors.has(nextCursor)) {
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
  const {
    response: page,
    rows: visibleRows,
    followedCursors,
  } = await accumulateUniqueListContinuations({
    initialResponse: initialPage,
    rowsFromResponse: (response) =>
      Array.isArray(response?.result) ? response.result : [],
    metadataFromResponse: continuationMetadata,
    identityFromRow: rowIdentity,
    knownIdentities,
    targetRowCount: isFreshChainRead ? 1 : pageSize,
    nextResponse: requestPage,
    onContinuation: (metadata) => {
      const nextCursor = getAttributeKeyNextCursor(metadata);
      if (nextCursor) knownCursors.add(nextCursor);
    },
    isCurrent: () => !signal?.aborted,
    cancellationSignal: signal,
    startedAt: actionStartedAt,
    // The backend already bounds each physical key read at four seconds. Do
    // not silently open a second physical request in the same click: publish
    // an empty advancing checkpoint and let the explicit signed-cursor action
    // continue it, preserving both the <5s gesture SLA and full reachability.
    maxContinuations: 0,
    maxElapsedMs: ATTRIBUTE_KEY_REQUEST_TIMEOUT_MS,
  });
  const normalized = checkedMetadata(page);

  return {
    ...normalized,
    // Transport-only and duplicate-only rows are never published to picker
    // consumers. If this bounded action stopped at an advancing checkpoint,
    // next_cursor remains available for the next explicit Load more action.
    result: visibleRows,
    // Store only cursors consumed by this chunk. Copying the cumulative cursor
    // history onto every page makes long sparse catalogs grow quadratically.
    [FOLLOWED_CURSORS_KEY]: followedCursors,
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
