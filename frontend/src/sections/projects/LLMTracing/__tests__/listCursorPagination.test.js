import { describe, expect, it, vi } from "vitest";

import {
  collectExactListRows,
  createListCursorPagination,
  followEmptyListContinuations,
  getEmptyListContinuation,
  loadExactListPage,
  listContinuationParams,
  LIST_CURSOR_CONTINUATION_LIMIT_ERROR_CODE,
  LIST_CURSOR_MODES,
  resumeEmptyListPage,
} from "../listCursorPagination";

describe("list cursor pagination", () => {
  const exactResponse = (rows, hasMore, nextCursor) => ({
    rows,
    metadata: { has_more: hasMore, next_cursor: nextCursor },
  });

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

  it("rejects a cursor replay even when it crosses visible pages", () => {
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
    ).toThrow("repeated continuation cursor");
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
    expect(resumedContinuation).toHaveBeenCalledWith("resume-after-outage");
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

  it("restarts safely in numbered mode when a cursor hits a legacy API pod", () => {
    const pagination = createListCursorPagination();
    pagination.recordResponse(0, {
      has_more: true,
      next_cursor: "signed-page-1",
    });
    const cursorGeneration = pagination.generation();

    expect(
      pagination.canRecoverFromContinuationError(1, {
        response: { status: 400 },
      }),
    ).toBe(true);
    expect(
      pagination.canRecoverFromContinuationError(1, {
        response: { status: 422 },
      }),
    ).toBe(true);
    expect(
      pagination.canRecoverFromContinuationError(1, {
        response: { status: 503 },
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
});
