import { describe, expect, it, vi } from "vitest";

import {
  accumulateUniqueListContinuations,
  collectExactListRows,
  createListCursorPagination,
  followEmptyListContinuations,
  getEmptyListContinuation,
  isLegacyListCursorValidationError,
  loadExactListPage,
  listContinuationParams,
  LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE,
  LIST_CURSOR_MODES,
  rememberBoundedListCursorIdentity,
  requestListWithLegacyCursorFallback,
  resumeEmptyListPage,
  shareInFlightListPage,
} from "../listCursorPagination";

describe("list cursor pagination", () => {
  const exactResponse = (rows, hasMore, nextCursor, nextCursorFingerprint) => ({
    rows,
    metadata: {
      has_more: hasMore,
      next_cursor: nextCursor,
      ...(nextCursorFingerprint === undefined
        ? {}
        : { next_cursor_fingerprint: nextCursorFingerprint }),
    },
  });

  const cursorFingerprint = (character) => character.repeat(64);

  const loadExactPage = ({
    pagination,
    pageNumber = 0,
    responses,
    targetRowCount = 25,
    ...options
  }) => {
    let responseIndex = 0;
    return loadExactListPage({
      pagination,
      pageNumber,
      targetRowCount,
      loadResponse: async () => responses[responseIndex++],
      nextResponse: async () => responses[responseIndex++],
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      rowIdentity: (row) => row.id,
      ...options,
    });
  };

  it("shares an in-flight visible-page load and releases it after settlement", async () => {
    const inFlight = new Map();
    let resolveLoad;
    const load = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveLoad = resolve;
        }),
    );

    const first = shareInFlightListPage({
      inFlight,
      key: "7:0",
      load,
    });
    const duplicate = shareInFlightListPage({
      inFlight,
      key: "7:0",
      load,
    });

    expect(duplicate).toBe(first);
    await Promise.resolve();
    expect(load).toHaveBeenCalledOnce();

    resolveLoad("page");
    await expect(Promise.all([first, duplicate])).resolves.toEqual([
      "page",
      "page",
    ]);
    await Promise.resolve();
    expect(inFlight.has("7:0")).toBe(false);

    await expect(
      shareInFlightListPage({
        inFlight,
        key: "7:0",
        load: () => Promise.resolve("fresh"),
      }),
    ).resolves.toBe("fresh");
  });

  const legacyUnknownFieldError = (field = "cursor_mode") => ({
    response: {
      status: 400,
      data: {
        status: false,
        type: "validation_error",
        code: "invalid",
        attr: field,
        detail: `${field}: Unknown field.`,
        details: { [field]: ["Unknown field."] },
      },
    },
  });

  it("recognizes only legacy unknown cursor-field validation errors", () => {
    expect(
      isLegacyListCursorValidationError(legacyUnknownFieldError("cursor_mode")),
    ).toBe(true);
    expect(
      isLegacyListCursorValidationError(legacyUnknownFieldError("cursor")),
    ).toBe(true);
    expect(
      isLegacyListCursorValidationError({
        response: {
          status: 400,
          data: { detail: "cursor: The continuation cursor is invalid." },
        },
      }),
    ).toBe(false);
    expect(
      isLegacyListCursorValidationError({
        response: {
          status: 400,
          data: {
            attr: "filters",
            detail: "filters: Unknown field.",
            details: { filters: ["Unknown field."] },
          },
        },
      }),
    ).toBe(false);
  });

  it("bounds retained preview cursor identities and rejects repeats", () => {
    const identities = new Set();
    rememberBoundedListCursorIdentity(identities, "boundary-a", 2);
    rememberBoundedListCursorIdentity(identities, "boundary-b", 2);

    expect(() =>
      rememberBoundedListCursorIdentity(identities, "boundary-c", 2),
    ).toThrow("history safety limit");
    expect(() =>
      rememberBoundedListCursorIdentity(identities, "boundary-a", 2),
    ).toThrow("repeated continuation cursor");
    expect(identities).toEqual(new Set(["boundary-a", "boundary-b"]));
  });

  it("never translates a rejected continuation cursor into first-page numbered data", async () => {
    const error = legacyUnknownFieldError("cursor");
    const request = vi.fn().mockRejectedValue(error);

    await expect(
      requestListWithLegacyCursorFallback({
        request,
        params: {
          project_id: "p1",
          cursor_mode: true,
          cursor: "signed-continuation",
        },
      }),
    ).rejects.toEqual(error);
    expect(request).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledWith({
      project_id: "p1",
      cursor_mode: true,
      cursor: "signed-continuation",
    });
  });

  it("retries page zero once without cursor fields on a legacy API", async () => {
    const pagination = createListCursorPagination();
    const calls = [];
    let attempt = 0;

    const page = await loadExactListPage({
      pagination,
      pageNumber: 0,
      targetRowCount: 25,
      loadResponse: async () => {
        calls.push(pagination.requestParams(0, { project_id: "p1" }));
        attempt += 1;
        if (attempt === 1) throw legacyUnknownFieldError();
        return { rows: [{ id: "legacy-row" }], metadata: { total_rows: 1 } };
      },
      nextResponse: vi.fn(),
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      rowIdentity: (row) => row.id,
    });

    expect(calls).toEqual([
      { project_id: "p1", cursor_mode: true, page_number: 0 },
      { project_id: "p1", page_number: 0 },
    ]);
    expect(page.rows).toEqual([{ id: "legacy-row" }]);
    expect(page.stale).toBe(false);
    expect(pagination.mode()).toBe(LIST_CURSOR_MODES.NUMBERED);
  });

  it("does not loop when the legacy numbered retry also fails", async () => {
    const pagination = createListCursorPagination();
    const loadResponse = vi.fn().mockRejectedValue(legacyUnknownFieldError());

    await expect(
      loadExactListPage({
        pagination,
        pageNumber: 0,
        targetRowCount: 25,
        loadResponse,
        nextResponse: vi.fn(),
        rowsFromResponse: () => [],
        metadataFromResponse: () => ({}),
        rowIdentity: (row) => row.id,
      }),
    ).rejects.toEqual(legacyUnknownFieldError());
    expect(loadResponse).toHaveBeenCalledTimes(2);
  });

  it("keeps the new-backend cursor contract without a legacy retry", async () => {
    const pagination = createListCursorPagination();
    const loadResponse = vi
      .fn()
      .mockResolvedValue(
        exactResponse([{ id: "new-row" }], true, "signed-next"),
      );

    const page = await loadExactListPage({
      pagination,
      pageNumber: 0,
      targetRowCount: 1,
      loadResponse,
      nextResponse: vi.fn(),
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      rowIdentity: (row) => row.id,
    });

    expect(loadResponse).toHaveBeenCalledTimes(1);
    expect(page.rows).toEqual([{ id: "new-row" }]);
    expect(pagination.mode()).toBe(LIST_CURSOR_MODES.CURSOR);
    expect(pagination.requestParams(1, {})).toEqual({
      cursor_mode: true,
      cursor: "signed-next",
    });
  });

  it("opts page zero into cursor mode while preserving page-zero compatibility", () => {
    const pagination = createListCursorPagination();

    expect(pagination.requestParams(0, { project_id: "p1" })).toEqual({
      project_id: "p1",
      cursor_mode: true,
      page_number: 0,
    });
  });

  it("uses the returned opaque cursor and omits page_number", () => {
    const pagination = createListCursorPagination();
    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "signed-page-1",
    });

    expect(pagination.mode()).toBe(LIST_CURSOR_MODES.CURSOR);
    expect(pagination.requestParams(1, { project_id: "p1" })).toEqual({
      project_id: "p1",
      cursor_mode: true,
      cursor: "signed-page-1",
    });
  });

  it("builds a preview continuation without either numbered-page field", () => {
    expect(
      listContinuationParams(
        { project_id: "p1", page: 1, page_number: 0, page_size: 50 },
        "signed-next",
      ),
    ).toEqual({
      project_id: "p1",
      page_size: 50,
      cursor_mode: true,
      cursor: "signed-next",
    });
  });

  it("adapts the same cursor chain to a one-based page parameter", () => {
    const pagination = createListCursorPagination({
      pageParam: "page",
      pageOffset: 1,
    });

    expect(pagination.requestParams(0, { project_id: "p1" })).toEqual({
      project_id: "p1",
      cursor_mode: true,
      page: 1,
    });
    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "signed-voice-page-2",
    });
    expect(pagination.requestParams(1, { project_id: "p1" })).toEqual({
      project_id: "p1",
      cursor_mode: true,
      cursor: "signed-voice-page-2",
    });
  });

  it("bounds completed page replay with least-recently-used eviction", () => {
    const pagination = createListCursorPagination({ maxCompletedPages: 2 });
    const completed = (id) => ({
      rows: [{ id }],
      response: { data: { table: [] } },
      metadata: { has_more: false, next_cursor: null },
      isLastPage: true,
      canPrefetch: false,
    });

    pagination.cacheCompletedVisiblePage(0, completed("page-0"));
    pagination.cacheCompletedVisiblePage(1, completed("page-1"));
    expect(pagination.completedVisiblePage(0)?.rows).toEqual([
      { id: "page-0" },
    ]);
    pagination.cacheCompletedVisiblePage(2, completed("page-2"));

    expect(pagination.completedVisiblePage(1)).toBeNull();
    expect(pagination.completedVisiblePage(0)?.rows).toEqual([
      { id: "page-0" },
    ]);
    expect(pagination.completedVisiblePage(2)?.rows).toEqual([
      { id: "page-2" },
    ]);
  });

  it("invalidates the continuation chain when the grid query resets", () => {
    const pagination = createListCursorPagination();
    const staleGeneration = pagination.generation();
    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "stale-cursor",
    });
    pagination.reset();

    expect(pagination.mode()).toBe(LIST_CURSOR_MODES.UNKNOWN);
    expect(pagination.isCurrent(staleGeneration)).toBe(false);
    expect(pagination.requestParams(1, { project_id: "p1" })).toEqual({
      project_id: "p1",
      page_number: 1,
    });
  });

  it("fails closed when a cursor response claims another page without a token", () => {
    const pagination = createListCursorPagination();

    expect(() =>
      pagination.recordResponse(0, {
        has_more: true,
        next_cursor: null,
      }),
    ).toThrow("omitted its continuation cursor");
  });

  it("falls back to numbered pages when page zero is served by a legacy API", () => {
    const pagination = createListCursorPagination();
    pagination.recordResponse(0, { total_rows: 100 });

    expect(pagination.mode()).toBe(LIST_CURSOR_MODES.NUMBERED);
    expect(pagination.requestParams(1, { project_id: "p1" })).toEqual({
      project_id: "p1",
      page_number: 1,
    });
  });

  it("honors has_more=false even when the terminal page is full", () => {
    const pagination = createListCursorPagination();
    const metadata = { has_more: false, next_cursor: null };
    pagination.recordResponse(0, metadata);

    expect(pagination.isLastPage(metadata, 25, 25)).toBe(true);
  });

  it("rejects a terminal replay that becomes nonterminal", () => {
    const pagination = createListCursorPagination();
    pagination.recordResponse(0, { has_more: false, next_cursor: null });

    expect(() =>
      pagination.recordResponse(0, {
        has_more: true,
        next_cursor: "unexpected-successor",
      }),
    ).toThrow("changed a proven continuation boundary");
  });

  it("rejects a nonterminal replay that becomes terminal", () => {
    const pagination = createListCursorPagination();
    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "proven-successor",
    });

    expect(() =>
      pagination.recordResponse(0, { has_more: false, next_cursor: null }),
    ).toThrow("changed a proven continuation boundary");
    expect(pagination.requestParams(1, {}).cursor).toBe("proven-successor");
  });

  it("rejects a legacy page-zero replay after cursor mode begins", () => {
    const pagination = createListCursorPagination();
    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "page-1",
    });

    expect(() => pagination.recordResponse(0, { total_rows: 25 })).toThrow(
      "legacy list API",
    );
    expect(pagination.mode()).toBe(LIST_CURSOR_MODES.CURSOR);
    expect(pagination.requestParams(1, {}).cursor).toBe("page-1");
  });

  it("keeps checkpoint-only hops on the same visible page until rows arrive", async () => {
    const pagination = createListCursorPagination();
    const responses = [
      { rows: [], metadata: { has_more: true, next_cursor: "checkpoint-1" } },
      { rows: [], metadata: { has_more: true, next_cursor: "checkpoint-2" } },
      {
        rows: [{ trace_id: "trace-old" }],
        metadata: { has_more: true, next_cursor: "after-rows" },
      },
    ];
    let responseIndex = 0;
    const requestedParams = [];

    const response = await followEmptyListContinuations({
      initialResponse: responses[responseIndex],
      rowsFromResponse: (value) => value.rows,
      metadataFromResponse: (value) => value.metadata,
      onContinuation: (metadata) =>
        pagination.recordEmptyContinuation(0, metadata),
      nextResponse: async () => {
        requestedParams.push(pagination.requestParams(0, { page_size: 25 }));
        responseIndex += 1;
        return responses[responseIndex];
      },
    });

    expect(response.rows).toEqual([{ trace_id: "trace-old" }]);
    expect(requestedParams).toEqual([
      { page_size: 25, cursor_mode: true, cursor: "checkpoint-1" },
      { page_size: 25, cursor_mode: true, cursor: "checkpoint-2" },
    ]);
    pagination.recordResponse(0, response.metadata);
    expect(pagination.requestParams(0, { page_size: 25 })).toEqual({
      page_size: 25,
      cursor_mode: true,
      page_number: 0,
    });
    expect(pagination.requestParams(1, { page_size: 25 })).toEqual({
      page_size: 25,
      cursor_mode: true,
      cursor: "after-rows",
    });
  });

  it("accepts a terminal empty adaptive-search window without another request", async () => {
    const nextResponse = vi.fn();
    const terminal = {
      rows: [],
      metadata: {
        browse_status: "exhausted",
        has_more: false,
        next_cursor: null,
      },
    };

    const response = await followEmptyListContinuations({
      initialResponse: terminal,
      rowsFromResponse: (value) => value.rows,
      metadataFromResponse: (value) => value.metadata,
      nextResponse,
    });

    expect(response).toBe(terminal);
    expect(nextResponse).not.toHaveBeenCalled();
    expect(
      getEmptyListContinuation(response.rows, response.metadata),
    ).toBeNull();
  });

  it("fails closed instead of looping on a repeated empty cursor", async () => {
    await expect(
      followEmptyListContinuations({
        initialResponse: {
          rows: [],
          metadata: { has_more: true, next_cursor: "same" },
        },
        rowsFromResponse: (value) => value.rows,
        metadataFromResponse: (value) => value.metadata,
        nextResponse: async () => ({
          rows: [],
          metadata: { has_more: true, next_cursor: "same" },
        }),
      }),
    ).rejects.toThrow("repeated continuation cursor");
  });

  it("rejects a non-adjacent cursor cycle persisted across bounded attempts", () => {
    const pagination = createListCursorPagination();

    pagination.recordEmptyContinuation(0, {
      has_more: true,
      next_cursor: "checkpoint-a",
    });
    pagination.recordEmptyContinuation(0, {
      has_more: true,
      next_cursor: "checkpoint-b",
    });

    expect(() =>
      pagination.recordEmptyContinuation(0, {
        has_more: true,
        next_cursor: "checkpoint-a",
      }),
    ).toThrow("repeated continuation cursor");
  });

  it("allows an evicted visible page to replay the same proven cursor edge", () => {
    const pagination = createListCursorPagination();

    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "page-1-start",
    });

    expect(() =>
      pagination.recordResponse(0, {
        has_more: true,
        next_cursor: "page-1-start",
      }),
    ).not.toThrow();
    expect(pagination.requestParams(1, { page_size: 25 })).toEqual({
      page_size: 25,
      cursor_mode: true,
      cursor: "page-1-start",
    });
  });

  it("re-loads an evicted exact block without corrupting its cursor chain", async () => {
    const pagination = createListCursorPagination();
    const response = exactResponse(
      Array.from({ length: 25 }, (_, index) => ({ id: index + 1 })),
      true,
      "page-1-start",
    );

    const first = await loadExactPage({
      pagination,
      responses: [response],
    });
    const replay = await loadExactPage({
      pagination,
      responses: [response],
    });

    expect(replay.rows).toEqual(first.rows);
    expect(pagination.requestParams(1, { page_size: 25 })).toEqual({
      page_size: 25,
      cursor_mode: true,
      cursor: "page-1-start",
    });
  });

  it("accepts a timestamp-rotated token when an evicted page is replayed", () => {
    const pagination = createListCursorPagination();
    const firstToken = "opaque-token-first";
    const rotatedToken = "opaque-token-rotated";
    const stableBoundary = cursorFingerprint("a");

    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: firstToken,
      next_cursor_fingerprint: stableBoundary,
    });

    expect(() =>
      pagination.recordResponse(0, {
        has_more: true,
        next_cursor: rotatedToken,
        next_cursor_fingerprint: stableBoundary,
      }),
    ).not.toThrow();
    expect(pagination.requestParams(1, { page_size: 25 })).toEqual({
      page_size: 25,
      cursor_mode: true,
      cursor: rotatedToken,
    });
  });

  it("rejects a replay whose stable fingerprint changes its proven boundary", () => {
    const pagination = createListCursorPagination();

    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "opaque-page-1-a",
      next_cursor_fingerprint: cursorFingerprint("a"),
    });

    expect(() =>
      pagination.recordResponse(0, {
        has_more: true,
        next_cursor: "opaque-page-1-b",
        next_cursor_fingerprint: cursorFingerprint("b"),
      }),
    ).toThrow("changed a proven continuation boundary");
  });

  it("re-loads an LRU-evicted block when the signer rotates next_cursor", async () => {
    const pagination = createListCursorPagination({ maxCompletedPages: 1 });
    const firstToken = "opaque-token-first";
    const rotatedToken = "opaque-token-rotated";
    const stableBoundary = cursorFingerprint("c");
    const firstPage = exactResponse(
      [{ id: "page-0" }],
      true,
      firstToken,
      stableBoundary,
    );

    await loadExactPage({
      pagination,
      responses: [firstPage],
      targetRowCount: 1,
    });
    pagination.cacheCompletedVisiblePage(1, {
      rows: [{ id: "page-1" }],
      response: exactResponse([{ id: "page-1" }], false, null),
      metadata: { has_more: false, next_cursor: null },
      isLastPage: true,
      canPrefetch: false,
    });
    expect(pagination.completedVisiblePage(0)).toBeNull();

    const replay = await loadExactPage({
      pagination,
      responses: [
        exactResponse([{ id: "page-0" }], true, rotatedToken, stableBoundary),
      ],
      targetRowCount: 1,
    });

    expect(replay.rows).toEqual([{ id: "page-0" }]);
    expect(pagination.requestParams(1, { page_size: 1 })).toEqual({
      page_size: 1,
      cursor_mode: true,
      cursor: rotatedToken,
    });
  });

  it("hard-bounds unique boundaries without pruning backward replay proofs", () => {
    const pagination = createListCursorPagination({
      maxCompletedPages: 1,
      maxCursorCheckpoints: 2,
    });

    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "page-1",
    });
    pagination.recordResponse(1, {
      has_more: true,
      next_cursor: "page-2",
    });

    expect(() =>
      pagination.recordResponse(2, {
        has_more: true,
        next_cursor: "page-3",
      }),
    ).toThrow("history safety limit");
    expect(pagination.requestParams(1, {}).cursor).toBe("page-1");
    expect(pagination.requestParams(2, {}).cursor).toBe("page-2");
    expect(() =>
      pagination.recordResponse(0, {
        has_more: true,
        next_cursor: "page-1",
      }),
    ).not.toThrow();

    expect(pagination.retainedStateCounts()).toMatchObject({
      pageCursors: 3,
      cursorBoundaries: 2,
      cursorTransitions: 2,
      transportCursors: 0,
    });
  });

  it("fails closed before an over-cap cycle can revisit an evicted fingerprint", () => {
    const pagination = createListCursorPagination({
      maxCompletedPages: 1,
      maxCursorCheckpoints: 2,
    });
    const cycle = [
      ["cycle-a-token", cursorFingerprint("a")],
      ["cycle-b-token", cursorFingerprint("b")],
      ["cycle-c-token", cursorFingerprint("c")],
      ["cycle-a-rotated-token", cursorFingerprint("a")],
    ];

    expect(() => {
      for (const [nextCursor, nextCursorFingerprint] of cycle) {
        pagination.recordEmptyContinuation(0, {
          has_more: true,
          next_cursor: nextCursor,
          next_cursor_fingerprint: nextCursorFingerprint,
        });
      }
    }).toThrow("history safety limit");

    expect(pagination.retainedStateCounts()).toMatchObject({
      cursorBoundaries: 2,
      cursorTransitions: 2,
      transportCursors: 1,
    });
    expect(pagination.requestParams(0, {}).cursor).toBe("cycle-b-token");
  });

  it("hard-bounds sparse continuation boundaries", () => {
    const sparsePagination = createListCursorPagination({
      maxCompletedPages: 1,
      maxCursorCheckpoints: 2,
    });
    sparsePagination.recordEmptyContinuation(0, {
      has_more: true,
      next_cursor: "sparse-0",
    });
    sparsePagination.recordEmptyContinuation(0, {
      has_more: true,
      next_cursor: "sparse-1",
    });
    expect(() =>
      sparsePagination.recordEmptyContinuation(0, {
        has_more: true,
        next_cursor: "sparse-2",
      }),
    ).toThrow("history safety limit");

    expect(sparsePagination.retainedStateCounts()).toMatchObject({
      cursorBoundaries: 2,
      cursorTransitions: 2,
      transportCursors: 1,
    });
    expect(sparsePagination.requestParams(0, {}).cursor).toBe("sparse-1");
  });

  it("bounds sparse buffered pages to the visible row-cache window", () => {
    const pagination = createListCursorPagination({
      maxCompletedPages: 2,
      maxCursorCheckpoints: 8,
    });

    const metadataByPage = Array.from({ length: 3 }, (_, pageNumber) => ({
      has_more: true,
      next_cursor: `page-${pageNumber + 1}`,
    }));
    for (const [pageNumber, metadata] of metadataByPage.entries()) {
      pagination.recordResponse(pageNumber, metadata);
    }
    for (const pageNumber of [2, 1, 0]) {
      const metadata = metadataByPage[pageNumber];
      pagination.recordVisibleContinuation(pageNumber, metadata, {
        rows: [{ id: `partial-${pageNumber}` }],
        response: exactResponse([], true, metadata.next_cursor),
      });
    }

    expect(pagination.retainedStateCounts()).toMatchObject({
      bufferedPages: 2,
      transportCursors: 2,
    });
    expect(pagination.bufferedVisiblePage(2)).toBeNull();
    expect(pagination.bufferedVisiblePage(1)?.rows).toEqual([
      { id: "partial-1" },
    ]);
    expect(pagination.bufferedVisiblePage(0)?.rows).toEqual([
      { id: "partial-0" },
    ]);
  });

  it("rejects a continuation for a page without an input cursor", () => {
    const pagination = createListCursorPagination();

    pagination.recordEmptyContinuation(0, {
      has_more: true,
      next_cursor: "page-scoped-token",
    });
    expect(() =>
      pagination.recordEmptyContinuation(1, {
        has_more: true,
        next_cursor: "page-scoped-token",
      }),
    ).toThrow("unavailable for this page");
  });

  it("rejects a non-advancing next-page response cursor", () => {
    const pagination = createListCursorPagination();

    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "cursor-consumed-by-page-1",
    });
    expect(() =>
      pagination.recordResponse(1, {
        has_more: true,
        next_cursor: "cursor-consumed-by-page-1",
      }),
    ).toThrow("repeated continuation cursor");
  });

  it("resumes a buffered partial page from its live signed checkpoint", async () => {
    const pagination = createListCursorPagination();
    const loadResponse = vi.fn().mockResolvedValue({
      rows: [{ id: "row-1" }],
      metadata: { has_more: true, next_cursor: "resume-after-outage" },
    });
    const outage = new Error("network unavailable");
    const failedContinuation = vi.fn().mockRejectedValue(outage);

    await expect(
      loadExactListPage({
        pagination,
        pageNumber: 0,
        targetRowCount: 2,
        loadResponse,
        nextResponse: failedContinuation,
        rowsFromResponse: (value) => value.rows,
        metadataFromResponse: (value) => value.metadata,
        rowIdentity: (row) => row.id,
      }),
    ).rejects.toBe(outage);

    const resumedContinuation = vi.fn().mockResolvedValue({
      rows: [{ id: "row-2" }],
      metadata: { has_more: false, next_cursor: null },
    });
    await expect(
      loadExactListPage({
        pagination,
        pageNumber: 0,
        targetRowCount: 2,
        loadResponse,
        nextResponse: resumedContinuation,
        rowsFromResponse: (value) => value.rows,
        metadataFromResponse: (value) => value.metadata,
        rowIdentity: (row) => row.id,
      }),
    ).resolves.toMatchObject({
      rows: [{ id: "row-1" }, { id: "row-2" }],
      pending: false,
      isLastPage: true,
    });
    expect(loadResponse).toHaveBeenCalledOnce();
    expect(resumedContinuation).toHaveBeenCalledWith(
      "resume-after-outage",
      expect.any(AbortSignal),
    );
  });

  it("preserves a valid sparse continuation at its hop bound", async () => {
    const pagination = createListCursorPagination();
    let cursorIndex = 0;
    const response = await followEmptyListContinuations({
      initialResponse: {
        rows: [],
        metadata: { has_more: true, next_cursor: "checkpoint-0" },
      },
      rowsFromResponse: (value) => value.rows,
      metadataFromResponse: (value) => value.metadata,
      maxContinuations: 2,
      nextResponse: async () => {
        cursorIndex += 1;
        return {
          rows: [],
          metadata: {
            has_more: true,
            next_cursor: `checkpoint-${cursorIndex}`,
          },
        };
      },
    });

    expect(response).toEqual({
      rows: [],
      metadata: { has_more: true, next_cursor: "checkpoint-2" },
    });
    expect(cursorIndex).toBe(2);
    expect(getEmptyListContinuation(response.rows, response.metadata)).toBe(
      "checkpoint-2",
    );
    pagination.recordEmptyContinuation(0, response.metadata);
    expect(pagination.requestParams(0, { page_size: 25 })).toEqual({
      page_size: 25,
      cursor_mode: true,
      cursor: "checkpoint-2",
    });
  });

  it("preserves a valid sparse continuation at its time bound", async () => {
    let elapsedMs = 0;
    const response = await followEmptyListContinuations({
      initialResponse: {
        rows: [],
        metadata: { has_more: true, next_cursor: "checkpoint-0" },
      },
      rowsFromResponse: (value) => value.rows,
      metadataFromResponse: (value) => value.metadata,
      maxElapsedMs: 50,
      now: () => elapsedMs,
      nextResponse: async () => {
        elapsedMs = 75;
        return {
          rows: [],
          metadata: { has_more: true, next_cursor: "checkpoint-1" },
        };
      },
    });

    expect(response).toEqual({
      rows: [],
      metadata: { has_more: true, next_cursor: "checkpoint-1" },
    });
  });

  it("aborts an in-flight continuation when the shared action deadline expires", async () => {
    let continuationSignal;
    const initialResponse = {
      rows: [],
      metadata: { has_more: true, next_cursor: "checkpoint-1" },
    };

    const response = await followEmptyListContinuations({
      initialResponse,
      rowsFromResponse: (value) => value.rows,
      metadataFromResponse: (value) => value.metadata,
      maxElapsedMs: 5,
      nextResponse: (_cursor, signal) => {
        continuationSignal = signal;
        return new Promise(() => {});
      },
    });

    expect(response).toBe(initialResponse);
    expect(continuationSignal.aborted).toBe(true);
  });

  it("keeps accumulated rows and the retryable cursor when a fill continuation times out", async () => {
    let continuationSignal;
    const initialResponse = {
      rows: [{ key: "one" }],
      metadata: { has_more: true, next_cursor: "checkpoint-1" },
    };

    const result = await accumulateUniqueListContinuations({
      initialResponse,
      rowsFromResponse: (value) => value.rows,
      metadataFromResponse: (value) => value.metadata,
      identityFromRow: ({ key }) => key,
      targetRowCount: 10,
      maxElapsedMs: 5,
      nextResponse: (_cursor, signal) => {
        continuationSignal = signal;
        return new Promise(() => {});
      },
    });

    expect(result.response).toBe(initialResponse);
    expect(result.rows).toEqual([{ key: "one" }]);
    expect(result.followedCursors).toEqual([]);
    expect(result.response.metadata.next_cursor).toBe("checkpoint-1");
    expect(continuationSignal.aborted).toBe(true);
  });

  it("schedules an AG Grid retry without advancing the visible page", () => {
    const pagination = createListCursorPagination();
    const resume = vi.fn();
    const schedule = vi.fn((callback) => callback());

    expect(
      resumeEmptyListPage({
        rows: [],
        metadata: { has_more: true, next_cursor: "checkpoint-rare" },
        pagination,
        pageNumber: 0,
        resume,
        schedule,
      }),
    ).toBe(true);

    expect(schedule).toHaveBeenCalledTimes(1);
    expect(resume).toHaveBeenCalledTimes(1);
    expect(pagination.requestParams(0, { page_size: 25 })).toEqual({
      page_size: 25,
      cursor_mode: true,
      cursor: "checkpoint-rare",
    });
  });

  it("preserves a page-N start cursor across transient empty checkpoints", () => {
    const pagination = createListCursorPagination();
    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "page-1-start",
    });
    pagination.recordEmptyContinuation(1, {
      has_more: true,
      next_cursor: "page-1-checkpoint",
    });
    expect(pagination.requestParams(1, { page_size: 25 }).cursor).toBe(
      "page-1-checkpoint",
    );

    pagination.recordResponse(1, {
      has_more: true,
      next_cursor: "page-2-start",
    });
    expect(pagination.requestParams(1, { page_size: 25 }).cursor).toBe(
      "page-1-start",
    );
    expect(pagination.requestParams(2, { page_size: 25 }).cursor).toBe(
      "page-2-start",
    );
  });

  it("recovers only from strict legacy cursor evidence", () => {
    const pagination = createListCursorPagination();
    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "signed-page-1",
    });
    const cursorGeneration = pagination.generation();

    expect(
      pagination.canRecoverFromContinuationError(1, {
        response: {
          status: 400,
          data: { details: { cursor: ["Unknown field."] } },
        },
      }),
    ).toBe(true);
    expect(
      pagination.canRecoverFromContinuationError(1, {
        response: { status: 400, data: { code: "invalid_cursor" } },
      }),
    ).toBe(false);
    expect(
      pagination.canRecoverFromContinuationError(1, {
        response: { status: 400, data: { code: "cursor_expired" } },
      }),
    ).toBe(false);
    expect(
      pagination.canRecoverFromContinuationError(1, {
        response: { status: 400, data: { code: "cursor_mismatch" } },
      }),
    ).toBe(false);
    expect(
      pagination.canRecoverFromContinuationError(1, {
        response: { status: 422 },
      }),
    ).toBe(false);

    pagination.disableCursor();

    expect(pagination.isCurrent(cursorGeneration)).toBe(false);
    expect(pagination.mode()).toBe(LIST_CURSOR_MODES.NUMBERED);
    expect(pagination.requestParams(0, { project_id: "p1" })).toEqual({
      project_id: "p1",
      page_number: 0,
    });
  });

  it("restarts instead of accepting a legacy success as a cursor page", () => {
    const pagination = createListCursorPagination();
    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "signed-page-1",
    });

    let mixedVersionError;
    try {
      pagination.recordResponse(1, { total_rows: 100 });
    } catch (error) {
      mixedVersionError = error;
    }

    expect(mixedVersionError).toBeInstanceOf(Error);
    expect(
      pagination.canRecoverFromContinuationError(1, mixedVersionError),
    ).toBe(true);
    expect(pagination.mode()).toBe(LIST_CURSOR_MODES.CURSOR);
  });

  it("fills a visible page from non-empty short transport responses", async () => {
    const pagination = createListCursorPagination();
    const page = await loadExactPage({
      pagination,
      responses: [
        exactResponse([{ id: 1 }], true, "after-1"),
        exactResponse(
          Array.from({ length: 24 }, (_, index) => ({ id: index + 2 })),
          false,
          null,
        ),
      ],
    });

    expect(page.rows.map(({ id }) => id)).toEqual(
      Array.from({ length: 25 }, (_, index) => index + 1),
    );
    expect(page.pending).toBe(false);
    expect(page.isLastPage).toBe(true);
  });

  it("does not publish no-results while exact empty checkpoints still have more", async () => {
    const pagination = createListCursorPagination();
    const page = await loadExactPage({
      pagination,
      targetRowCount: 1,
      responses: [
        exactResponse([], true, "checkpoint-1"),
        exactResponse([], true, "checkpoint-2"),
        exactResponse([], true, "checkpoint-3"),
        exactResponse([{ id: "older-match" }], false, null),
      ],
    });

    expect(page.rows).toEqual([{ id: "older-match" }]);
    expect(page.pending).toBe(false);
    expect(page.isLastPage).toBe(true);
  });

  it("does not write a stale response cursor into a reset query generation", async () => {
    const pagination = createListCursorPagination();
    const requestGeneration = pagination.generation();
    const page = await loadExactListPage({
      pagination,
      pageNumber: 0,
      targetRowCount: 1,
      loadResponse: async () => {
        pagination.reset();
        return exactResponse([{ id: "stale-row" }], true, "stale-cursor");
      },
      nextResponse: async () => {
        throw new Error("A stale request must not continue");
      },
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      rowIdentity: (row) => row.id,
      isCurrent: () => pagination.isCurrent(requestGeneration),
    });

    expect(page.stale).toBe(true);
    expect(page.rows).toEqual([]);
    expect(pagination.mode()).toBe(LIST_CURSOR_MODES.UNKNOWN);
    expect(pagination.requestParams(0, { page_size: 1 })).toEqual({
      page_size: 1,
      cursor_mode: true,
      page_number: 0,
    });
  });

  it("carries overflow into the next visible page without a skip", async () => {
    const pagination = createListCursorPagination();
    const firstPage = await loadExactPage({
      pagination,
      responses: [
        exactResponse([{ id: 1 }], true, "after-1"),
        exactResponse(
          Array.from({ length: 25 }, (_, index) => ({ id: index + 2 })),
          true,
          "after-26",
        ),
      ],
    });

    expect(firstPage.rows.map(({ id }) => id)).toEqual(
      Array.from({ length: 25 }, (_, index) => index + 1),
    );
    expect(firstPage.canPrefetch).toBe(false);
    expect(pagination.requestParams(1, { page_size: 25 }).cursor).toBe(
      "after-26",
    );

    const secondPage = await loadExactPage({
      pagination,
      pageNumber: 1,
      responses: [exactResponse([{ id: 27 }, { id: 28 }], false, null)],
    });
    expect(secondPage.rows.map(({ id }) => id)).toEqual([26, 27, 28]);
    expect(secondPage.isLastPage).toBe(true);
  });

  it("publishes terminal overflow on the next page without another request", async () => {
    const pagination = createListCursorPagination();
    const firstPage = await loadExactPage({
      pagination,
      responses: [
        exactResponse([{ id: 1 }], true, "after-1"),
        exactResponse(
          Array.from({ length: 25 }, (_, index) => ({ id: index + 2 })),
          false,
          null,
        ),
      ],
    });
    expect(firstPage.isLastPage).toBe(false);
    expect(firstPage.canPrefetch).toBe(false);

    const loadResponse = vi.fn();
    const secondPage = await loadExactListPage({
      pagination,
      pageNumber: 1,
      targetRowCount: 25,
      loadResponse,
      nextResponse: vi.fn(),
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      rowIdentity: (row) => row.id,
    });
    expect(loadResponse).not.toHaveBeenCalled();
    expect(secondPage.rows).toEqual([{ id: 26 }]);
    expect(secondPage.isLastPage).toBe(true);
  });

  it("publishes a full nonterminal overflow page without an eager transport request", async () => {
    const pagination = createListCursorPagination();
    const firstPage = await loadExactPage({
      pagination,
      responses: [
        exactResponse(
          Array.from({ length: 50 }, (_, index) => ({ id: index + 1 })),
          true,
          "after-50",
        ),
      ],
    });
    expect(firstPage.rows).toHaveLength(25);
    expect(firstPage.isLastPage).toBe(false);
    expect(firstPage.canPrefetch).toBe(false);

    const loadResponse = vi.fn();
    const secondPage = await loadExactListPage({
      pagination,
      pageNumber: 1,
      targetRowCount: 25,
      loadResponse,
      nextResponse: vi.fn(),
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      rowIdentity: (row) => row.id,
    });
    expect(loadResponse).not.toHaveBeenCalled();
    expect(secondPage.rows.map(({ id }) => id)).toEqual(
      Array.from({ length: 25 }, (_, index) => index + 26),
    );
    expect(secondPage.isLastPage).toBe(false);
    expect(pagination.requestParams(2, { page_size: 25 }).cursor).toBe(
      "after-50",
    );
  });

  it("reuses a completed page without replaying its signed cursor", async () => {
    const pagination = createListCursorPagination();
    const rows = Array.from({ length: 25 }, (_, index) => ({
      id: index + 1,
    }));
    const firstPage = await loadExactPage({
      pagination,
      responses: [exactResponse(rows, true, "after-25")],
    });
    const replayLoad = vi.fn(() =>
      Promise.resolve(exactResponse(rows, true, "changed-successor")),
    );

    const revisitedPage = await loadExactListPage({
      pagination,
      pageNumber: 0,
      targetRowCount: 25,
      loadResponse: replayLoad,
      nextResponse: vi.fn(),
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      rowIdentity: (row) => row.id,
    });

    expect(replayLoad).not.toHaveBeenCalled();
    expect(revisitedPage.rows).toEqual(firstPage.rows);
    expect(revisitedPage.isLastPage).toBe(false);
    expect(pagination.requestParams(1, { page_size: 25 }).cursor).toBe(
      "after-25",
    );
  });

  it("retains only the compacted response for completed and buffered pages", async () => {
    const pagination = createListCursorPagination();
    const hugeTransportPayload = "x".repeat(2_000_000);
    const responses = [
      {
        rows: [{ id: 1 }],
        metadata: { has_more: true, next_cursor: "after-1" },
        hugeTransportPayload,
      },
      {
        rows: [{ id: 2 }],
        metadata: { has_more: false, next_cursor: null },
        hugeTransportPayload,
      },
    ];
    let responseIndex = 0;
    const compactResponse = vi.fn((response) => ({
      metadata: response.metadata,
      retainedMarker: response.rows[0].id,
    }));

    const first = await loadExactListPage({
      pagination,
      pageNumber: 0,
      targetRowCount: 2,
      loadResponse: async () => responses[responseIndex++],
      nextResponse: async () => responses[responseIndex++],
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      compactResponse,
      rowIdentity: (row) => row.id,
    });

    expect(first.rows).toEqual([{ id: 1 }, { id: 2 }]);
    expect(first.response).toEqual({
      metadata: { has_more: false, next_cursor: null },
      retainedMarker: 2,
    });
    expect(first.response).not.toHaveProperty("hugeTransportPayload");

    const revisited = await loadExactListPage({
      pagination,
      pageNumber: 0,
      targetRowCount: 2,
      loadResponse: vi.fn(),
      nextResponse: vi.fn(),
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      compactResponse,
      rowIdentity: (row) => row.id,
    });
    expect(revisited.response).toEqual(first.response);
    expect(compactResponse).toHaveBeenCalledTimes(2);
  });

  it("drops the Axios request handle from cached responses by default", async () => {
    const pagination = createListCursorPagination();
    const request = { responseText: "x".repeat(2_000_000) };
    const response = {
      data: {
        rows: [{ id: 1 }],
        metadata: { has_more: false, next_cursor: null },
      },
      request,
      status: 200,
    };

    const page = await loadExactListPage({
      pagination,
      pageNumber: 0,
      targetRowCount: 25,
      loadResponse: async () => response,
      nextResponse: vi.fn(),
      rowsFromResponse: (value) => value.data.rows,
      metadataFromResponse: (value) => value.data.metadata,
      rowIdentity: (row) => row.id,
    });

    expect(page.response).toEqual({
      data: response.data,
      status: 200,
    });
    expect(page.response).not.toHaveProperty("request");
  });

  it("deduplicates a replayed boundary row by stable identity", async () => {
    const pagination = createListCursorPagination();
    const page = await loadExactPage({
      pagination,
      targetRowCount: 2,
      responses: [
        exactResponse([{ id: 1 }], true, "after-1"),
        exactResponse([{ id: 1 }, { id: 2 }], false, null),
      ],
    });

    expect(page.rows).toEqual([{ id: 1 }, { id: 2 }]);
  });

  it("fails closed at the continuation hop bound instead of auto-looping", async () => {
    const pagination = createListCursorPagination();
    let limitError;
    try {
      await loadExactPage({
        pagination,
        targetRowCount: 3,
        maxContinuations: 1,
        responses: [
          exactResponse([{ id: 1 }], true, "after-1"),
          exactResponse([{ id: 2 }], true, "after-2"),
        ],
      });
    } catch (error) {
      limitError = error;
    }

    expect(limitError).toBeInstanceOf(Error);
    expect(limitError.code).toBe(LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE);
    // The exact checkpoint is retained, but this request does not schedule an
    // unbounded automatic retry and never publishes the two-row partial page.
    expect(pagination.requestParams(0, { page_size: 3 }).cursor).toBe(
      "after-2",
    );
  });

  it("fails closed at the continuation deadline instead of auto-looping", async () => {
    const pagination = createListCursorPagination();
    let elapsedMs = 0;
    let responseIndex = 0;
    const responses = [
      exactResponse([], true, "after-empty"),
      exactResponse([{ id: 1 }], true, "after-1"),
    ];
    let limitError;

    try {
      await loadExactListPage({
        pagination,
        pageNumber: 0,
        targetRowCount: 2,
        maxElapsedMs: 50,
        now: () => elapsedMs,
        loadResponse: async () => responses[responseIndex++],
        nextResponse: async () => {
          elapsedMs = 75;
          return responses[responseIndex++];
        },
        rowsFromResponse: (response) => response.rows,
        metadataFromResponse: (response) => response.metadata,
        rowIdentity: (row) => row.id,
      });
    } catch (error) {
      limitError = error;
    }

    expect(limitError).toBeInstanceOf(Error);
    expect(limitError.code).toBe(LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE);
    expect(pagination.requestParams(0, { page_size: 2 }).cursor).toBe(
      "after-1",
    );
  });

  it("aborts a hung initial exact-page request at the shared deadline", async () => {
    const pagination = createListCursorPagination();
    let requestSignal;

    await expect(
      loadExactListPage({
        pagination,
        pageNumber: 0,
        targetRowCount: 1,
        maxElapsedMs: 5,
        loadResponse: (signal) => {
          requestSignal = signal;
          return new Promise(() => {});
        },
        nextResponse: vi.fn(),
        rowsFromResponse: (response) => response.rows,
        metadataFromResponse: (response) => response.metadata,
        rowIdentity: (row) => row.id,
      }),
    ).rejects.toMatchObject({
      code: LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE,
    });
    expect(requestSignal.aborted).toBe(true);
  });

  it("aborts an in-flight exact-page request when its grid generation resets", async () => {
    const pagination = createListCursorPagination();
    let requestSignal;
    let markStarted;
    const started = new Promise((resolve) => {
      markStarted = resolve;
    });
    const page = loadExactListPage({
      pagination,
      pageNumber: 0,
      targetRowCount: 1,
      loadResponse: (signal) => {
        requestSignal = signal;
        markStarted();
        return new Promise((_resolve, reject) => {
          signal.addEventListener(
            "abort",
            () =>
              reject(
                Object.assign(new Error("canceled"), { code: "ERR_CANCELED" }),
              ),
            { once: true },
          );
        });
      },
      nextResponse: vi.fn(),
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      rowIdentity: (row) => row.id,
    });

    await started;
    pagination.reset();

    expect(requestSignal.aborted).toBe(true);
    await expect(page).rejects.toMatchObject({ code: "ERR_CANCELED" });
  });

  it("fails closed when a non-empty continuation repeats its cursor", async () => {
    const pagination = createListCursorPagination();
    await expect(
      loadExactPage({
        pagination,
        targetRowCount: 3,
        responses: [
          exactResponse([{ id: 1 }], true, "same"),
          exactResponse([{ id: 2 }], true, "same"),
        ],
      }),
    ).rejects.toThrow("repeated continuation cursor");
  });

  it("fails closed when re-signed preview cursors repeat one boundary", async () => {
    const boundary = cursorFingerprint("c");
    await expect(
      collectExactListRows({
        initialResponse: exactResponse(
          [{ id: 1 }],
          true,
          "signed-token-1",
          boundary,
        ),
        targetRowCount: 3,
        rowsFromResponse: (response) => response.rows,
        metadataFromResponse: (response) => response.metadata,
        nextResponse: async () =>
          exactResponse([{ id: 2 }], true, "signed-token-2", boundary),
        rowIdentity: (row) => row.id,
      }),
    ).rejects.toThrow("repeated continuation cursor");
  });

  it("collects an exact fixed-size preview across short responses", async () => {
    const responses = [
      exactResponse([{ id: 1 }], true, "after-1"),
      exactResponse([{ id: 2 }, { id: 3 }], true, "after-3"),
    ];
    const page = await collectExactListRows({
      initialResponse: responses[0],
      targetRowCount: 3,
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      nextResponse: async () => responses[1],
      rowIdentity: (row) => row.id,
    });
    expect(page.rows).toEqual([{ id: 1 }, { id: 2 }, { id: 3 }]);
    expect(page.pending).toBe(false);
    expect(page.nextCursor).toBe("after-3");
    expect(page.nextCursorIdentity).toBe("opaque-token:after-3");
  });

  it("returns resumable rows and cursor when a preview hits its hop bound", async () => {
    const page = await collectExactListRows({
      initialResponse: exactResponse([{ id: 2 }], true, "after-2"),
      initialRows: [{ id: 1 }],
      targetRowCount: 4,
      maxContinuations: 1,
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      nextResponse: async () => exactResponse([{ id: 3 }], true, "after-3"),
      rowIdentity: (row) => row.id,
    });
    expect(page.rows).toEqual([{ id: 1 }, { id: 2 }, { id: 3 }]);
    expect(page.pending).toBe(true);
    expect(page.nextCursor).toBe("after-3");
  });

  it("aborts a hung preview continuation and preserves its checkpoint", async () => {
    const initialResponse = exactResponse([{ id: 1 }], true, "after-1");
    let requestSignal;

    const page = await collectExactListRows({
      initialResponse,
      targetRowCount: 2,
      maxElapsedMs: 5,
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      nextResponse: (_cursor, signal) => {
        requestSignal = signal;
        return new Promise(() => {});
      },
      rowIdentity: (row) => row.id,
    });

    expect(page).toMatchObject({
      response: initialResponse,
      rows: [{ id: 1 }],
      pending: true,
      stale: false,
      nextCursor: "after-1",
      nextCursorIdentity: "opaque-token:after-1",
    });
    expect(requestSignal.aborted).toBe(true);
  });

  it.each([
    { has_more: false, next_cursor: "unexpected" },
    {
      has_more: false,
      next_cursor: null,
      next_cursor_fingerprint: cursorFingerprint("d"),
    },
    { has_more: "yes", next_cursor: "after-1" },
    { has_more: true },
  ])("rejects invalid exact-preview cursor metadata: %o", async (metadata) => {
    await expect(
      collectExactListRows({
        initialResponse: { rows: [{ id: 1 }], metadata },
        targetRowCount: 1,
        rowsFromResponse: (response) => response.rows,
        metadataFromResponse: (response) => response.metadata,
        nextResponse: vi.fn(),
        rowIdentity: (row) => row.id,
      }),
    ).rejects.toThrow(/cursor metadata|continuation cursor/);
  });

  it("keeps accepting a legacy exact-preview response with no cursor fields", async () => {
    const page = await collectExactListRows({
      initialResponse: { rows: [{ id: 1 }], metadata: {} },
      targetRowCount: 1,
      rowsFromResponse: (response) => response.rows,
      metadataFromResponse: (response) => response.metadata,
      nextResponse: vi.fn(),
      rowIdentity: (row) => row.id,
    });

    expect(page.rows).toEqual([{ id: 1 }]);
    expect(page.nextCursor).toBeNull();
  });
});
