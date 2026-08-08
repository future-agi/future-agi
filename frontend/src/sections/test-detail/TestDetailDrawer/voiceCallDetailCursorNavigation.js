import { listContinuationParams } from "src/sections/projects/LLMTracing/listCursorPagination";

const DEFAULT_MAX_RESPONSES_PER_ATTEMPT = 13;
const DEFAULT_ATTEMPT_DEADLINE_MS = 30_000;
const REQUEST_DEADLINE_REACHED = Symbol("voice-call-request-deadline");

const hasOwn = (value, key) =>
  Object.prototype.hasOwnProperty.call(value || {}, key);

const payloadFromResponse = (response) => {
  const body = response?.data ?? response ?? {};
  return body?.result ?? body;
};

const rowsFromResponse = (response) => {
  const payload = payloadFromResponse(response);
  return payload?.results || payload?.data || payload?.calls || [];
};

const rowIdentity = (row) => row?.call_id || row?.id || row?.trace_id || null;

const cleanBaseParams = (baseParams) => {
  const {
    page: _page,
    page_number: _pageNumber,
    cursor: _cursor,
    cursor_mode: _cursorMode,
    ...params
  } = baseParams || {};
  return params;
};

const normalizeNonNegativeNumber = (value, fallback = 0) => {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : fallback;
};

export const canNavigateToNextVoiceCallDetail = ({
  rowIndex,
  totalCount,
  totalCountIsLowerBound,
}) => {
  if (totalCountIsLowerBound === true) return true;
  if (totalCount === null || totalCount === undefined || totalCount === "") {
    return true;
  }
  const exactTotal = Number(totalCount);
  if (!Number.isFinite(exactTotal)) return true;
  return normalizeNonNegativeNumber(rowIndex) < exactTotal - 1;
};

export const getLegacyVoiceCallNavigationTotal = ({
  rowIndex,
  totalCount,
  totalCountIsLowerBound,
}) => {
  if (totalCountIsLowerBound !== true) return totalCount;
  return Math.max(
    normalizeNonNegativeNumber(totalCount),
    normalizeNonNegativeNumber(rowIndex) + 2,
  );
};

export const createVoiceCallDetailRequestGuard = () => {
  let generation = 0;
  let active = false;

  return {
    activate: () => {
      active = true;
      generation += 1;
    },
    begin: () => {
      generation += 1;
      return generation;
    },
    invalidate: () => {
      generation += 1;
    },
    isCurrent: (requestGeneration) =>
      active && requestGeneration === generation,
    dispose: () => {
      active = false;
      generation += 1;
    },
  };
};

const requestWithinDeadline = async ({ request, params, remainingMs }) => {
  const controller = new AbortController();
  let timeoutId;
  const deadline = new Promise((resolve) => {
    timeoutId = setTimeout(() => {
      resolve(REQUEST_DEADLINE_REACHED);
      queueMicrotask(() => controller.abort());
    }, remainingMs);
  });
  const response = Promise.resolve().then(() =>
    request(params, { signal: controller.signal }),
  );

  try {
    const result = await Promise.race([response, deadline]);
    return result === REQUEST_DEADLINE_REACHED
      ? { deadlineReached: true, response: null }
      : { deadlineReached: false, response: result };
  } finally {
    clearTimeout(timeoutId);
  }
};

/**
 * Resolve a voice-call row by its exact zero-based list index.
 *
 * The navigator deliberately owns a single forward-only cursor chain. It can
 * therefore satisfy a drawer cache miss without ever translating the visible
 * row index into a numbered deep-page ClickHouse request. Sparse bounded
 * responses are transport checkpoints, not empty pages: the navigator follows
 * them until it reaches the requested row, a terminal response, or its local
 * safety bound. Progress is retained so an explicit retry resumes at the last
 * signed checkpoint instead of restarting from page one.
 */
export const createVoiceCallDetailCursorNavigator = ({
  request,
  baseParams,
  pageSize,
  maxResponsesPerAttempt = DEFAULT_MAX_RESPONSES_PER_ATTEMPT,
  maxElapsedMs = DEFAULT_ATTEMPT_DEADLINE_MS,
  now = () => Date.now(),
}) => {
  if (typeof request !== "function") {
    throw new Error("Voice-call cursor request is required");
  }
  if (!Number.isInteger(pageSize) || pageSize < 1) {
    throw new Error("Invalid voice-call cursor page size");
  }
  if (!Number.isInteger(maxResponsesPerAttempt) || maxResponsesPerAttempt < 1) {
    throw new Error("Invalid voice-call cursor response limit");
  }
  if (!Number.isFinite(maxElapsedMs) || maxElapsedMs < 1) {
    throw new Error("Invalid voice-call cursor deadline");
  }

  const params = cleanBaseParams(baseParams);
  const rows = [];
  const identities = new Set();
  const followedCursors = new Set();
  let started = false;
  let terminal = false;
  let nextCursor = null;

  const appendRows = (nextRows) => {
    for (const row of Array.isArray(nextRows) ? nextRows : []) {
      const identity = rowIdentity(row);
      if (
        (typeof identity !== "string" && typeof identity !== "number") ||
        String(identity).length === 0
      ) {
        throw new Error("Voice-call row is missing a stable identity");
      }
      const key = `${typeof identity}:${String(identity)}`;
      if (!identities.has(key)) {
        identities.add(key);
        rows.push(row);
      }
    }
  };

  const resultFor = (index, { pending = false } = {}) => ({
    row: rows[index] ?? null,
    pending,
    terminal,
    loadedRowCount: rows.length,
  });

  const loadRow = async (index) => {
    if (!Number.isInteger(index) || index < 0) {
      throw new Error("Invalid voice-call row index");
    }
    if (rows[index]) return resultFor(index);
    if (terminal) return resultFor(index);

    const startedAt = now();
    let responseCount = 0;

    while (!terminal && !rows[index]) {
      if (
        responseCount >= maxResponsesPerAttempt ||
        now() - startedAt >= maxElapsedMs
      ) {
        return resultFor(index, { pending: true });
      }

      const requestParams = !started
        ? {
            ...params,
            page_size: pageSize,
            cursor_mode: true,
            page: 1,
          }
        : listContinuationParams(
            { ...params, page_size: pageSize },
            nextCursor,
          );
      const remainingMs = Math.max(0, maxElapsedMs - (now() - startedAt));
      if (remainingMs === 0) {
        return resultFor(index, { pending: true });
      }
      const requestResult = await requestWithinDeadline({
        request,
        params: requestParams,
        remainingMs,
      });
      if (requestResult.deadlineReached) {
        return resultFor(index, { pending: true });
      }
      const response = requestResult.response;
      responseCount += 1;
      started = true;

      appendRows(rowsFromResponse(response));
      const payload = payloadFromResponse(response);
      if (!hasOwn(payload, "has_more") || !hasOwn(payload, "next_cursor")) {
        throw new Error("Voice-call list does not support exact cursors");
      }

      if (payload.has_more === false) {
        if (payload.next_cursor != null) {
          throw new Error("Voice-call list returned invalid cursor metadata");
        }
        terminal = true;
        nextCursor = null;
        break;
      }

      if (
        payload.has_more !== true ||
        typeof payload.next_cursor !== "string" ||
        payload.next_cursor.length === 0
      ) {
        throw new Error("Voice-call list omitted its continuation cursor");
      }
      if (followedCursors.has(payload.next_cursor)) {
        throw new Error("Voice-call list repeated its continuation cursor");
      }
      followedCursors.add(payload.next_cursor);
      nextCursor = payload.next_cursor;
    }

    return resultFor(index);
  };

  return {
    loadRow,
    loadedRowCount: () => rows.length,
    isTerminal: () => terminal,
  };
};
