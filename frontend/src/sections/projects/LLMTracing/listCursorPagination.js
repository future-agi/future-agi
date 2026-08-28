import {
  ANALYTICS_REQUEST_TIMEOUT_MS,
  CURSOR_MAX_EMPTY_CONTINUATIONS,
} from "src/config/runtime_limits";
import { isGridApiLive } from "src/utils/gridApi";

const CURSOR_MODE = "cursor";
const NUMBERED_MODE = "numbered";
const UNKNOWN_MODE = "unknown";
const MIXED_VERSION_ERROR_CODE = "LIST_CURSOR_MIXED_VERSION";
export const LIST_CURSOR_PROTOCOL_ERROR_CODE = "LIST_CURSOR_PROTOCOL";
export const LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE =
  "LIST_CURSOR_CONTINUATION_LIMIT";
export const LIST_CURSOR_CONTINUATION_NOTICE =
  "Preparing exact results. Refresh or retry to continue.";
const DEFAULT_MAX_EMPTY_CONTINUATIONS = CURSOR_MAX_EMPTY_CONTINUATIONS;
const DEFAULT_EMPTY_CONTINUATION_DEADLINE_MS = ANALYTICS_REQUEST_TIMEOUT_MS;

const requestWithinDeadline = async ({
  request,
  cancellationSignal,
  remainingMs,
}) => {
  const requestController = new AbortController();
  const cancelRequest = () => requestController.abort();
  cancellationSignal?.addEventListener("abort", cancelRequest, {
    once: true,
  });
  if (cancellationSignal?.aborted) cancelRequest();

  let timer;
  try {
    const result = await Promise.race([
      Promise.resolve()
        .then(() => request(requestController.signal))
        .then((response) => ({ completed: true, response })),
      new Promise((resolve) => {
        timer = setTimeout(() => resolve({ completed: false }), remainingMs);
      }),
    ]);
    if (!result.completed) requestController.abort();
    return result;
  } finally {
    clearTimeout(timer);
    cancellationSignal?.removeEventListener("abort", cancelRequest);
  }
};

const requestContinuationWithinDeadline = ({
  nextResponse,
  nextCursor,
  ...deadlineOptions
}) =>
  requestWithinDeadline({
    request: (signal) => nextResponse(nextCursor, signal),
    ...deadlineOptions,
  });

const createListCursorContinuationLimitError = () => {
  const error = new Error("Exact list continuation safety limit reached");
  error.code = LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE;
  return error;
};

const hasOwn = (value, key) =>
  Object.prototype.hasOwnProperty.call(value || {}, key);

const LEGACY_CURSOR_FIELDS = new Set(["cursor", "cursor_mode"]);

const unknownFieldMessage = (value) =>
  typeof value === "string" && /unknown field/i.test(value);

/**
 * Match only the strict-validation response emitted by API versions that
 * predate the additive list cursor fields. Invalid/expired cursors and other
 * HTTP 400 responses must remain visible to the caller.
 */
export const isLegacyListCursorValidationError = (error) => {
  if (error?.response?.status !== 400) return false;

  const body = error?.response?.data;
  if (!body || typeof body !== "object") return false;

  const details = body.details;
  if (details && typeof details === "object") {
    for (const field of LEGACY_CURSOR_FIELDS) {
      const messages = details[field];
      if (
        (Array.isArray(messages) && messages.some(unknownFieldMessage)) ||
        unknownFieldMessage(messages)
      ) {
        return true;
      }
    }
  }

  const attr = typeof body.attr === "string" ? body.attr : null;
  const messages = [body.detail, body.message, body.error, body.result].filter(
    (value) => typeof value === "string",
  );
  if (
    attr &&
    LEGACY_CURSOR_FIELDS.has(attr) &&
    messages.some(unknownFieldMessage)
  ) {
    return true;
  }

  return messages.some((message) =>
    /(?:^|\b)(?:cursor_mode|cursor)\s*:\s*unknown field\b/i.test(message),
  );
};

export const legacyNumberedListParams = (
  params,
  { pageParam = "page_number", firstPage = 0 } = {},
) => {
  if (typeof pageParam !== "string" || pageParam.length === 0) {
    throw new Error("Invalid legacy list page parameter");
  }
  if (!Number.isInteger(firstPage) || firstPage < 0) {
    throw new Error("Invalid legacy list first page");
  }
  const {
    cursor: _cursor,
    cursor_mode: _cursorMode,
    page: _page,
    page_number: _pageNumber,
    ...baseParams
  } = params || {};
  return { ...baseParams, [pageParam]: firstPage };
};

/** One-shot compatibility request for a strict pre-cursor API deployment. */
export const requestListWithLegacyCursorFallback = async ({
  request,
  params,
  pageParam = "page_number",
  firstPage = 0,
}) => {
  if (typeof request !== "function") {
    throw new Error("List request is required");
  }
  try {
    return await request(params);
  } catch (error) {
    const hasContinuationCursor =
      typeof params?.cursor === "string" && params.cursor.length > 0;
    if (hasContinuationCursor || !isLegacyListCursorValidationError(error)) {
      throw error;
    }
    return request(legacyNumberedListParams(params, { pageParam, firstPage }));
  }
};

export const isListCursorContinuationLimitError = (error) =>
  error?.code === LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE;

/**
 * Resume an AG Grid server-side block without resetting the exact cursor chain.
 *
 * `retryServerSideLoads` turns AG Grid's failed block back into a loading stub.
 * The refresh fallback supports older grid APIs while preserving the store and
 * the signed checkpoint owned by the datasource.
 */
export const retryServerSideCursorLoad = (api) => {
  if (!isGridApiLive(api)) return false;
  if (typeof api?.retryServerSideLoads === "function") {
    api.retryServerSideLoads();
    return true;
  }
  if (typeof api?.refreshServerSide === "function") {
    api.refreshServerSide({ purge: false });
    return true;
  }
  return false;
};

export const createListCursorProtocolError = (message) => {
  const error = new Error(message);
  error.code = LIST_CURSOR_PROTOCOL_ERROR_CODE;
  return error;
};

export const isListCursorProtocolError = (error) =>
  error?.code === LIST_CURSOR_PROTOCOL_ERROR_CODE ||
  error?.code === MIXED_VERSION_ERROR_CODE;

/**
 * Share one exact visible-page read between equivalent AG Grid requests.
 *
 * AG Grid can ask for the same server-side block while its first read is still
 * in flight. Exact cursor reads mutate one forward-only checkpoint, so running
 * those requests independently duplicates backend work and races that state.
 * The caller owns this map per datasource generation and clears it on reset.
 */
export const shareInFlightListPage = ({ inFlight, key, load }) => {
  if (!(inFlight instanceof Map)) {
    throw new Error("Exact list in-flight map is required");
  }
  if (typeof key !== "string" || key.length === 0) {
    throw new Error("Exact list in-flight key is required");
  }
  if (typeof load !== "function") {
    throw new Error("Exact list page loader is required");
  }

  const existing = inFlight.get(key);
  if (existing) return existing;

  const request = Promise.resolve().then(load);
  inFlight.set(key, request);
  const clear = () => {
    if (inFlight.get(key) === request) inFlight.delete(key);
  };
  request.then(clear, clear);
  return request;
};

/**
 * Keep the opaque continuation chain for one immutable grid query.
 *
 * Cursor pagination is opt-in. The first response decides the mode: explicit
 * cursor metadata enables keyset continuation; a legacy page-zero response
 * falls back to numbered pages. Once a cursor chain starts, every continuation
 * must reach a cursor-capable API pod, so backend rollout must finish before
 * the cursor-enabled frontend is released.
 */
export const createListCursorPagination = ({
  pageParam = "page_number",
  pageOffset = 0,
} = {}) => {
  if (typeof pageParam !== "string" || pageParam.length === 0) {
    throw new Error("Invalid list page parameter");
  }
  if (!Number.isInteger(pageOffset) || pageOffset < 0) {
    throw new Error("Invalid list page offset");
  }

  let mode = UNKNOWN_MODE;
  let generation = 0;
  let generationController = new AbortController();
  const cursorByPage = new Map([[0, null]]);
  const transportCursorByPage = new Map();
  const bufferedVisiblePageByPage = new Map();
  // A bounded AG Grid cache may legitimately re-read an evicted block. Record
  // the proven cursor transition graph so an identical replay is idempotent,
  // while a non-advancing cursor, cycle, or changed successor still fails
  // closed. The symbol represents the cursor-less first transport page.
  const initialCursor = Symbol("initial-list-cursor");
  const cursorSuccessorByInput = new Map();

  const inputCursorForPage = (pageNumber) => {
    const cursor =
      transportCursorByPage.get(pageNumber) || cursorByPage.get(pageNumber);
    if (cursor) return cursor;
    return pageNumber === 0 ? initialCursor : null;
  };

  const recordCursorTransition = (pageNumber, nextCursor) => {
    const inputCursor = inputCursorForPage(pageNumber);
    if (inputCursor === null) {
      throw createListCursorProtocolError(
        "Continuation cursor is unavailable for this page",
      );
    }
    if (inputCursor === nextCursor) {
      throw createListCursorProtocolError(
        "List API returned a repeated continuation cursor",
      );
    }

    const provenSuccessor = cursorSuccessorByInput.get(inputCursor);
    if (provenSuccessor !== undefined) {
      if (provenSuccessor !== nextCursor) {
        throw createListCursorProtocolError(
          "List API changed a proven continuation cursor",
        );
      }
      return;
    }

    const traversed = new Set();
    let cursor = nextCursor;
    while (cursor !== undefined && !traversed.has(cursor)) {
      if (cursor === inputCursor) {
        throw createListCursorProtocolError(
          "List API returned a repeated continuation cursor",
        );
      }
      traversed.add(cursor);
      cursor = cursorSuccessorByInput.get(cursor);
    }
    cursorSuccessorByInput.set(inputCursor, nextCursor);
  };

  const advanceGeneration = () => {
    generation += 1;
    generationController.abort();
    generationController = new AbortController();
  };

  const reset = () => {
    advanceGeneration();
    mode = UNKNOWN_MODE;
    cursorByPage.clear();
    cursorByPage.set(0, null);
    transportCursorByPage.clear();
    bufferedVisiblePageByPage.clear();
    cursorSuccessorByInput.clear();
  };

  // An initial request rejected by a strict pre-cursor serializer is still the
  // same logical grid generation. Switch transport contracts without making
  // the successful retry look stale to the caller's generation guard.
  const fallbackToNumbered = () => {
    mode = NUMBERED_MODE;
    cursorByPage.clear();
    cursorByPage.set(0, null);
    transportCursorByPage.clear();
    bufferedVisiblePageByPage.clear();
    cursorSuccessorByInput.clear();
  };

  const disableCursor = () => {
    advanceGeneration();
    fallbackToNumbered();
  };

  const requestParams = (pageNumber, baseParams) => {
    if (!Number.isInteger(pageNumber) || pageNumber < 0) {
      throw new Error("Invalid list page number");
    }

    if (pageNumber === 0) {
      const continuation = transportCursorByPage.get(0) || cursorByPage.get(0);
      if (mode === CURSOR_MODE && continuation) {
        return {
          ...baseParams,
          cursor_mode: true,
          cursor: continuation,
        };
      }
      if (mode === NUMBERED_MODE) {
        return {
          ...baseParams,
          [pageParam]: pageOffset,
        };
      }
      return {
        ...baseParams,
        cursor_mode: true,
        [pageParam]: pageOffset,
      };
    }

    const cursor =
      transportCursorByPage.get(pageNumber) || cursorByPage.get(pageNumber);
    if (mode === CURSOR_MODE) {
      if (!cursor) {
        throw createListCursorProtocolError(
          "Continuation cursor is unavailable for this page",
        );
      }
      return {
        ...baseParams,
        cursor_mode: true,
        cursor,
      };
    }

    // An old API response to page zero may not return cursor metadata. Preserve
    // the accepted numbered-page contract for that request chain. Deployment
    // still has to complete the backend rollout before enabling the frontend:
    // a chain that already received a cursor cannot safely switch modes.
    return {
      ...baseParams,
      [pageParam]: pageNumber + pageOffset,
    };
  };

  const recordResponse = (pageNumber, metadata) => {
    const hasCursorContract =
      hasOwn(metadata, "has_more") && hasOwn(metadata, "next_cursor");
    if (!hasCursorContract) {
      if (mode === CURSOR_MODE && pageNumber > 0) {
        const error = new Error(
          "Cursor continuation reached a legacy list API",
        );
        error.code = MIXED_VERSION_ERROR_CODE;
        throw error;
      }
      mode = NUMBERED_MODE;
      transportCursorByPage.delete(pageNumber);
      cursorByPage.delete(pageNumber + 1);
      return;
    }

    mode = CURSOR_MODE;
    if (metadata.has_more === true) {
      if (
        typeof metadata.next_cursor !== "string" ||
        metadata.next_cursor.length === 0
      ) {
        throw createListCursorProtocolError(
          "List response omitted its continuation cursor",
        );
      }
      recordCursorTransition(pageNumber, metadata.next_cursor);
      cursorByPage.set(pageNumber + 1, metadata.next_cursor);
      transportCursorByPage.delete(pageNumber);
      return;
    }

    if (metadata.has_more !== false || metadata.next_cursor != null) {
      throw createListCursorProtocolError(
        "List response returned invalid cursor metadata",
      );
    }
    cursorByPage.delete(pageNumber + 1);
    transportCursorByPage.delete(pageNumber);
  };

  // A bounded transport page may scan a proven candidate prefix without
  // finding a matching row. Keep the signed checkpoint on the same visible
  // grid page so the caller can follow it immediately; advancing the visible
  // page here would create an empty UI block and misalign later cursors.
  const recordEmptyContinuation = (pageNumber, metadata) => {
    if (
      metadata?.has_more !== true ||
      typeof metadata?.next_cursor !== "string" ||
      metadata.next_cursor.length === 0
    ) {
      throw createListCursorProtocolError(
        "Empty list continuation is unavailable",
      );
    }
    mode = CURSOR_MODE;
    recordCursorTransition(pageNumber, metadata.next_cursor);
    transportCursorByPage.set(pageNumber, metadata.next_cursor);
    cursorByPage.delete(pageNumber + 1);
  };

  const bufferedVisiblePage = (pageNumber) => {
    const buffered = bufferedVisiblePageByPage.get(pageNumber);
    if (!buffered) return null;
    return { ...buffered, rows: [...buffered.rows] };
  };

  const recordVisibleContinuation = (
    pageNumber,
    metadata,
    { rows, response },
  ) => {
    if (!Array.isArray(rows)) {
      throw new Error("Invalid buffered list rows");
    }
    recordEmptyContinuation(pageNumber, metadata);
    bufferedVisiblePageByPage.set(pageNumber, {
      rows: [...rows],
      response,
      metadata,
    });
  };

  const completeVisiblePage = (
    pageNumber,
    metadata,
    { overflowRows = [], response } = {},
  ) => {
    if (!Array.isArray(overflowRows)) {
      throw new Error("Invalid overflow list rows");
    }
    const existingBuffer = bufferedVisiblePageByPage.get(pageNumber);
    const reusesBufferedTransport =
      existingBuffer?.response === response &&
      existingBuffer?.metadata === metadata;
    bufferedVisiblePageByPage.delete(pageNumber);

    if (reusesBufferedTransport && metadata?.has_more === true) {
      // One backend response can contain enough overflow for multiple visible
      // UI pages. Moving its already-recorded checkpoint forward across those
      // in-memory pages is not a cursor replay because no transport request
      // consumed it yet.
      const assignedCursor = cursorByPage.get(pageNumber);
      if (
        typeof metadata.next_cursor !== "string" ||
        metadata.next_cursor.length === 0 ||
        assignedCursor !== metadata.next_cursor
      ) {
        throw createListCursorProtocolError(
          "Buffered list page lost its continuation cursor",
        );
      }
      cursorByPage.delete(pageNumber);
      cursorByPage.set(pageNumber + 1, metadata.next_cursor);
      transportCursorByPage.delete(pageNumber);
    } else {
      recordResponse(pageNumber, metadata);
    }
    if (overflowRows.length > 0) {
      bufferedVisiblePageByPage.set(pageNumber + 1, {
        rows: [...overflowRows],
        response,
        metadata,
      });
    }
  };

  const isLastPage = (metadata, rowCount, pageSize) => {
    if (mode === CURSOR_MODE && hasOwn(metadata, "has_more")) {
      return metadata.has_more === false;
    }
    return rowCount < pageSize;
  };

  return {
    reset,
    disableCursor,
    fallbackToNumbered,
    requestParams,
    recordResponse,
    recordEmptyContinuation,
    bufferedVisiblePage,
    recordVisibleContinuation,
    completeVisiblePage,
    isLastPage,
    mode: () => mode,
    generation: () => generation,
    isCurrent: (requestGeneration) => requestGeneration === generation,
    cancellationSignal: () => generationController.signal,
    canRecoverFromContinuationError: (pageNumber, error) =>
      mode === CURSOR_MODE &&
      pageNumber > 0 &&
      (error?.response?.status === 400 ||
        error?.response?.status === 422 ||
        error?.code === MIXED_VERSION_ERROR_CODE),
  };
};

const stableRowKey = (rowIdentity, row) => {
  const identity = rowIdentity(row);
  if (
    (typeof identity !== "string" && typeof identity !== "number") ||
    String(identity).length === 0
  ) {
    throw createListCursorProtocolError(
      "Exact list row is missing a stable identity",
    );
  }
  return `${typeof identity}:${String(identity)}`;
};

/**
 * Fill one visible list page from as many bounded transport responses as are
 * required. A backend response may be non-empty but still shorter than the
 * requested page while `has_more` remains true. Publishing that response to
 * AG Grid would make it infer end-of-data and hide every later match.
 *
 * Overflow is retained for the next visible page. The hop/time bound is a
 * hard safety boundary for this automatic read: returning a pending page and
 * immediately asking AG Grid to retry would reset the local counter and turn
 * an always-advancing sparse cursor into an endless loading loop. Fail closed
 * instead, while retaining the signed checkpoint in pagination state. A
 * deliberate grid refresh can start a new bounded exact attempt; an empty
 * transport page is never published as a genuine empty result.
 */
export const loadExactListPage = async ({
  pagination,
  pageNumber,
  targetRowCount,
  loadResponse,
  nextResponse,
  rowsFromResponse,
  metadataFromResponse,
  rowIdentity,
  isCurrent = () => true,
  cancellationSignal,
  maxContinuations = DEFAULT_MAX_EMPTY_CONTINUATIONS,
  maxElapsedMs = DEFAULT_EMPTY_CONTINUATION_DEADLINE_MS,
  now = () => Date.now(),
}) => {
  if (!pagination || typeof pagination.bufferedVisiblePage !== "function") {
    throw new Error("Exact list pagination is required");
  }
  if (!Number.isInteger(pageNumber) || pageNumber < 0) {
    throw new Error("Invalid exact list page number");
  }
  if (!Number.isInteger(targetRowCount) || targetRowCount < 1) {
    throw new Error("Invalid exact list target row count");
  }
  if (typeof rowIdentity !== "function") {
    throw new Error("Exact list row identity is required");
  }
  if (!Number.isInteger(maxContinuations) || maxContinuations < 1) {
    throw new Error("Invalid list continuation limit");
  }
  if (!Number.isFinite(maxElapsedMs) || maxElapsedMs < 1) {
    throw new Error("Invalid list continuation deadline");
  }
  const activeCancellationSignal =
    cancellationSignal ||
    (typeof pagination.cancellationSignal === "function"
      ? pagination.cancellationSignal()
      : undefined);

  let buffered = pagination.bufferedVisiblePage(pageNumber);
  const accumulatedRows = [];
  const identities = new Set();
  const appendRows = (rows) => {
    for (const row of Array.isArray(rows) ? rows : []) {
      const identity = stableRowKey(rowIdentity, row);
      if (!identities.has(identity)) {
        identities.add(identity);
        accumulatedRows.push(row);
      }
    }
  };
  appendRows(buffered?.rows);

  let response = buffered?.response;
  let metadata = buffered?.metadata || {};
  let continuationCount = 0;
  let legacyFallbackAttempted = false;
  const startedAt = now();

  // A terminal overflow from the previous visible page already contains all
  // rows for this page, so it must not issue a cursor-less transport request.
  let needsResponse =
    accumulatedRows.length < targetRowCount &&
    (!buffered || metadata?.has_more === true);
  while (needsResponse) {
    if (!isCurrent()) {
      return {
        response,
        rows: accumulatedRows,
        metadata,
        pending: true,
        stale: true,
        isLastPage: false,
        canPrefetch: false,
      };
    }
    // A prior bounded attempt or transport failure can leave a proven partial
    // page plus its signed checkpoint buffered. Resume from that checkpoint on
    // the very first request of the next attempt; replaying `loadResponse`
    // would re-read the old transport prefix and turn a transient outage into
    // a false repeated-cursor protocol error.
    const resumeBufferedCheckpoint =
      continuationCount === 0 && buffered && metadata?.has_more === true;
    const remainingMs = Math.floor(maxElapsedMs - (now() - startedAt));
    if (remainingMs < 1) {
      throw createListCursorContinuationLimitError();
    }
    let nextTransportResponse;
    try {
      const transport = await requestWithinDeadline({
        request:
          continuationCount === 0 && !resumeBufferedCheckpoint
            ? (signal) => loadResponse(signal)
            : (signal) => nextResponse(metadata.next_cursor, signal),
        cancellationSignal: activeCancellationSignal,
        remainingMs,
      });
      if (!transport.completed) {
        throw createListCursorContinuationLimitError();
      }
      nextTransportResponse = transport.response;
    } catch (error) {
      if (
        legacyFallbackAttempted ||
        !isLegacyListCursorValidationError(error)
      ) {
        throw error;
      }
      legacyFallbackAttempted = true;
      pagination.fallbackToNumbered();
      buffered = null;
      accumulatedRows.length = 0;
      identities.clear();
      response = undefined;
      metadata = {};
      continuationCount = 0;
      needsResponse = true;
      continue;
    }
    if (!isCurrent()) {
      return {
        response,
        rows: accumulatedRows,
        metadata,
        pending: true,
        stale: true,
        isLastPage: false,
        canPrefetch: false,
      };
    }
    response = nextTransportResponse;
    appendRows(rowsFromResponse(response));
    metadata = metadataFromResponse(response) || {};

    if (
      accumulatedRows.length >= targetRowCount ||
      metadata.has_more !== true
    ) {
      break;
    }

    pagination.recordVisibleContinuation(pageNumber, metadata, {
      rows: accumulatedRows,
      response,
    });
    if (
      continuationCount >= maxContinuations ||
      now() - startedAt >= maxElapsedMs
    ) {
      throw createListCursorContinuationLimitError();
    }
    continuationCount += 1;
    needsResponse = true;
  }

  const rows = accumulatedRows.slice(0, targetRowCount);
  const overflowRows = accumulatedRows.slice(targetRowCount);
  pagination.completeVisiblePage(pageNumber, metadata, {
    overflowRows,
    response,
  });
  const isLastPage =
    overflowRows.length === 0 &&
    pagination.isLastPage(metadata, rows.length, targetRowCount);
  return {
    response,
    rows,
    metadata,
    pending: false,
    stale: false,
    isLastPage,
    // Overflow already owns the next visible page. Its continuation cursor
    // starts *after* those buffered rows, so prefetching it as the next page
    // would either discard that response or replay the same cursor when the
    // buffered page later asks for its remaining rows.
    canPrefetch: !isLastPage && overflowRows.length === 0,
  };
};

export const resumePendingListPage = ({
  page,
  resume,
  schedule = queueMicrotask,
}) => {
  if (page?.pending !== true || page?.stale === true) return false;
  schedule(resume);
  return true;
};

/**
 * Collect an exact fixed-size preview without visible-page state. Callers may
 * persist `rows` plus `nextCursor` when `pending` is true and pass those rows
 * back as `initialRows` on the next bounded attempt.
 */
export const collectExactListRows = async ({
  initialResponse,
  initialRows = [],
  targetRowCount,
  rowsFromResponse,
  metadataFromResponse,
  nextResponse,
  rowIdentity,
  onContinuation,
  isCurrent = () => true,
  cancellationSignal,
  maxContinuations = DEFAULT_MAX_EMPTY_CONTINUATIONS,
  maxElapsedMs = DEFAULT_EMPTY_CONTINUATION_DEADLINE_MS,
  now = () => Date.now(),
}) => {
  if (!Number.isInteger(targetRowCount) || targetRowCount < 1) {
    throw new Error("Invalid exact list target row count");
  }
  if (typeof rowIdentity !== "function") {
    throw new Error("Exact list row identity is required");
  }
  if (!Number.isInteger(maxContinuations) || maxContinuations < 1) {
    throw new Error("Invalid list continuation limit");
  }
  if (!Number.isFinite(maxElapsedMs) || maxElapsedMs < 1) {
    throw new Error("Invalid list continuation deadline");
  }

  const rows = [];
  const identities = new Set();
  const appendRows = (nextRows) => {
    for (const row of Array.isArray(nextRows) ? nextRows : []) {
      const identity = stableRowKey(rowIdentity, row);
      if (!identities.has(identity)) {
        identities.add(identity);
        rows.push(row);
      }
    }
  };
  appendRows(initialRows);

  let response = initialResponse;
  let metadata = {};
  let continuationCount = 0;
  const followed = new Set();
  const startedAt = now();
  while (response) {
    appendRows(rowsFromResponse(response));
    metadata = metadataFromResponse(response) || {};
    const hasHasMore = hasOwn(metadata, "has_more");
    const hasNextCursor = hasOwn(metadata, "next_cursor");
    if (hasHasMore !== hasNextCursor) {
      throw createListCursorProtocolError(
        "List response returned incomplete cursor metadata",
      );
    }
    const hasCursorContract = hasHasMore && hasNextCursor;
    const hasMore = metadata.has_more === true;
    let nextCursor = null;
    if (hasCursorContract) {
      if (metadata.has_more !== true && metadata.has_more !== false) {
        throw createListCursorProtocolError(
          "List response returned invalid cursor metadata",
        );
      }
      if (hasMore) {
        nextCursor = metadata.next_cursor;
        if (typeof nextCursor !== "string" || nextCursor.length === 0) {
          throw createListCursorProtocolError(
            "List response omitted its continuation cursor",
          );
        }
      } else if (metadata.next_cursor != null) {
        throw createListCursorProtocolError(
          "List response returned invalid cursor metadata",
        );
      }
    }
    if (rows.length >= targetRowCount || !hasMore) {
      if (hasMore && followed.has(nextCursor)) {
        throw createListCursorProtocolError(
          "List API returned a repeated continuation cursor",
        );
      }
      return {
        response,
        rows: rows.slice(0, targetRowCount),
        metadata,
        pending: false,
        stale: false,
        // A full visible preview is not necessarily terminal. Preserve the
        // unconsumed signed checkpoint so callers can lazily request another
        // exact row without replaying the current transport page.
        nextCursor,
      };
    }
    if (!isCurrent()) {
      return {
        response,
        rows,
        metadata,
        pending: true,
        stale: true,
        nextCursor: metadata.next_cursor,
      };
    }
    if (followed.has(nextCursor)) {
      throw createListCursorProtocolError(
        "List API returned a repeated continuation cursor",
      );
    }
    if (
      continuationCount >= maxContinuations ||
      now() - startedAt >= maxElapsedMs
    ) {
      return {
        response,
        rows,
        metadata,
        pending: true,
        stale: false,
        nextCursor,
      };
    }
    followed.add(nextCursor);
    onContinuation?.(metadata);
    continuationCount += 1;
    const remainingMs = Math.floor(maxElapsedMs - (now() - startedAt));
    if (remainingMs < 1) {
      return {
        response,
        rows,
        metadata,
        pending: true,
        stale: false,
        nextCursor,
      };
    }
    const continuation = await requestContinuationWithinDeadline({
      nextResponse,
      nextCursor,
      cancellationSignal,
      remainingMs,
    });
    if (!continuation.completed) {
      return {
        response,
        rows,
        metadata,
        pending: true,
        stale: false,
        nextCursor,
      };
    }
    response = continuation.response;
  }

  throw createListCursorProtocolError("List continuation returned no response");
};

export const LIST_CURSOR_MODES = Object.freeze({
  CURSOR: CURSOR_MODE,
  NUMBERED: NUMBERED_MODE,
  UNKNOWN: UNKNOWN_MODE,
});

export const listContinuationParams = (baseParams, cursor) => {
  if (typeof cursor !== "string" || cursor.length === 0) {
    throw new Error("Invalid list continuation cursor");
  }
  const { page: _page, page_number: _pageNumber, ...query } = baseParams;
  return { ...query, cursor_mode: true, cursor };
};

/**
 * Return the signed checkpoint for a transport-only empty response.
 *
 * An empty table is not a user-visible empty result while `has_more` is true:
 * the bounded backend scan has only proved that its current candidate prefix
 * contains no matches. Callers must keep this cursor on the same visible page
 * and resume that page instead of publishing an empty row set.
 */
export const getEmptyListContinuation = (rows, metadata) => {
  if (
    Array.isArray(rows) &&
    rows.length === 0 &&
    metadata?.has_more === true &&
    typeof metadata?.next_cursor === "string" &&
    metadata.next_cursor.length > 0
  ) {
    return metadata.next_cursor;
  }
  return null;
};

/** Preserve and asynchronously resume a transport-only page for AG Grid. */
export const resumeEmptyListPage = ({
  rows,
  metadata,
  pagination,
  pageNumber,
  resume,
  schedule = queueMicrotask,
}) => {
  if (!getEmptyListContinuation(rows, metadata)) return false;
  pagination.recordEmptyContinuation(pageNumber, metadata);
  schedule(resume);
  return true;
};

/**
 * Follow checkpoint-only transport pages until the API returns genuine rows
 * or proves the cursor chain is exhausted.  Sparse filters can legitimately
 * classify a bounded prefix without finding a match; exposing that transport
 * page as an empty visible page would be both misleading and would strand
 * older matches behind it.
 */
export const followEmptyListContinuations = async ({
  initialResponse,
  rowsFromResponse,
  metadataFromResponse,
  nextResponse,
  onContinuation,
  isCurrent = () => true,
  cancellationSignal,
  maxContinuations = DEFAULT_MAX_EMPTY_CONTINUATIONS,
  maxElapsedMs = DEFAULT_EMPTY_CONTINUATION_DEADLINE_MS,
  now = () => Date.now(),
  startedAt = now(),
}) => {
  if (!Number.isInteger(maxContinuations) || maxContinuations < 1) {
    throw new Error("Invalid list continuation limit");
  }
  if (!Number.isFinite(maxElapsedMs) || maxElapsedMs < 1) {
    throw new Error("Invalid list continuation deadline");
  }
  let response = initialResponse;
  const followed = new Set();
  let rows = rowsFromResponse(response) || [];

  while (rows.length === 0) {
    const metadata = metadataFromResponse(response) || {};
    const nextCursor = metadata.next_cursor;
    if (
      metadata.has_more !== true ||
      typeof nextCursor !== "string" ||
      nextCursor.length === 0
    ) {
      return response;
    }
    if (!isCurrent()) return response;
    // A repeated checkpoint is a malformed continuation chain regardless of
    // whether this request has also reached its local hop/time budget.
    if (followed.has(nextCursor)) {
      throw createListCursorProtocolError(
        "List API returned a repeated continuation cursor",
      );
    }
    if (
      followed.size >= maxContinuations ||
      now() - startedAt >= maxElapsedMs
    ) {
      // Sparse exact filters can legitimately need more checkpoints than one
      // browser request should follow. Return the current transport page with
      // its signed continuation intact; the normal page/cursor flow can resume
      // from it without turning a valid sparse result into a user-visible
      // failure or starting an unbounded request fan-out.
      return response;
    }
    followed.add(nextCursor);
    onContinuation?.(metadata);
    const remainingMs = Math.floor(maxElapsedMs - (now() - startedAt));
    if (remainingMs < 1) return response;
    const next = await requestContinuationWithinDeadline({
      nextResponse,
      nextCursor,
      cancellationSignal,
      remainingMs,
    });
    if (!next.completed) return response;
    response = next.response;
    rows = rowsFromResponse(response) || [];
  }
  return response;
};

/**
 * Fill one visible picker page from bounded physical cursor pages.
 *
 * The final response remains the metadata authority, including its signed
 * next cursor. Only the returned row collection spans physical pages.
 */
export const accumulateUniqueListContinuations = async ({
  initialResponse,
  rowsFromResponse,
  metadataFromResponse,
  identityFromRow,
  knownIdentities = [],
  targetRowCount,
  nextResponse,
  onContinuation,
  isCurrent = () => true,
  cancellationSignal,
  maxContinuations = DEFAULT_MAX_EMPTY_CONTINUATIONS,
  maxElapsedMs = DEFAULT_EMPTY_CONTINUATION_DEADLINE_MS,
  now = () => Date.now(),
  startedAt = now(),
}) => {
  if (!Number.isInteger(targetRowCount) || targetRowCount < 1) {
    throw new Error("Invalid list continuation target row count");
  }
  if (!Number.isInteger(maxContinuations) || maxContinuations < 0) {
    throw new Error("Invalid list continuation limit");
  }
  if (!Number.isFinite(maxElapsedMs) || maxElapsedMs < 1) {
    throw new Error("Invalid list continuation deadline");
  }

  let response = initialResponse;
  const identities = new Set(knownIdentities);
  const followedCursors = new Set();
  const rows = [];
  const appendRows = () => {
    for (const row of rowsFromResponse(response) || []) {
      const identity = identityFromRow(row);
      if (identity == null || identities.has(identity)) continue;
      identities.add(identity);
      rows.push(row);
    }
  };

  appendRows();
  while (rows.length < targetRowCount) {
    const metadata = metadataFromResponse(response) || {};
    const nextCursor = metadata.next_cursor;
    if (
      metadata.has_more !== true ||
      typeof nextCursor !== "string" ||
      nextCursor.length === 0
    ) {
      break;
    }
    if (!isCurrent()) break;
    if (followedCursors.has(nextCursor)) {
      throw createListCursorProtocolError(
        "List API returned a repeated continuation cursor",
      );
    }
    if (
      followedCursors.size >= maxContinuations ||
      now() - startedAt >= maxElapsedMs
    ) {
      break;
    }

    const remainingMs = Math.floor(maxElapsedMs - (now() - startedAt));
    if (remainingMs < 1) break;
    const next = await requestContinuationWithinDeadline({
      nextResponse,
      nextCursor,
      cancellationSignal,
      remainingMs,
    });
    if (!next.completed) break;

    followedCursors.add(nextCursor);
    onContinuation?.(metadata);
    response = next.response;
    appendRows();
  }

  return { response, rows, followedCursors: [...followedCursors] };
};
