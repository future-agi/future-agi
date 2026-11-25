import { describe, expect, it, vi } from "vitest";

import {
  compactAttributeKeyRetryPage,
  getAttributeKeyNextCursor,
  getNextAttributeKeyPageParam,
  isAttributeKeyCursorChainStopped,
  isAttributeKeyCursorStopped,
  readAttributeKeyPage,
} from "../attributeKeyCursorPagination";

const page = (keys, overrides = {}) => ({
  result: keys.map((key) => ({ key, type: "string" })),
  browse_status: "continuation",
  has_more: true,
  next_cursor: "next",
  ...overrides,
});

describe("attribute key cursor pagination", () => {
  it("compacts a fresh retry without dropping older rows or type families", () => {
    const compacted = compactAttributeKeyRetryPage(
      {
        pages: [
          {
            result: [
              { key: "older.only", type: "string", types: ["string"] },
              { key: "mixed", type: "string", types: ["string"] },
            ],
          },
        ],
      },
      {
        result: [
          { key: "fresh.only", type: "boolean", types: ["boolean"] },
          { key: "mixed", type: "number", types: ["number"] },
        ],
        has_more: true,
        next_cursor: "fresh-cursor",
      },
    );

    expect(compacted.result.map(({ key }) => key)).toEqual([
      "older.only",
      "mixed",
      "fresh.only",
    ]);
    expect(compacted.result.find(({ key }) => key === "mixed").types).toEqual([
      "string",
      "number",
    ]);
    expect(compacted.next_cursor).toBe("fresh-cursor");
  });

  it("publishes advancing checkpoints one explicit request at a time", async () => {
    const requestPage = vi
      .fn()
      .mockResolvedValueOnce(page([], { next_cursor: "checkpoint-1" }))
      .mockResolvedValueOnce(page([], { next_cursor: "checkpoint-2" }))
      .mockResolvedValueOnce(
        page(["older.attribute"], {
          browse_status: "exhausted",
          has_more: false,
          next_cursor: null,
        }),
      );

    const firstPage = await readAttributeKeyPage({
      pageParam: null,
      requestPage,
      signal: new AbortController().signal,
    });
    expect(requestPage).toHaveBeenCalledTimes(1);
    expect(firstPage.result).toEqual([]);
    expect(firstPage.next_cursor).toBe("checkpoint-1");

    const secondPage = await readAttributeKeyPage({
      pageParam: firstPage.next_cursor,
      publishedData: { pages: [firstPage], pageParams: [null] },
      requestPage,
      signal: new AbortController().signal,
    });
    expect(requestPage).toHaveBeenCalledTimes(2);
    expect(secondPage.result).toEqual([]);
    expect(secondPage.next_cursor).toBe("checkpoint-2");

    const thirdPage = await readAttributeKeyPage({
      pageParam: secondPage.next_cursor,
      publishedData: {
        pages: [firstPage, secondPage],
        pageParams: [null, firstPage.next_cursor],
      },
      requestPage,
      signal: new AbortController().signal,
    });

    expect(requestPage.mock.calls.map(([cursor]) => cursor)).toEqual([
      null,
      "checkpoint-1",
      "checkpoint-2",
    ]);
    expect(thirdPage.result).toEqual([
      { key: "older.attribute", type: "string" },
    ]);
    expect(thirdPage.has_more).toBe(false);
    expect(getAttributeKeyNextCursor(thirdPage)).toBeUndefined();
  });

  it("treats terminal browse status as authoritative over stale has_more", () => {
    const terminal = page([], {
      browse_status: "exhausted",
      has_more: true,
      next_cursor: "must-not-load",
    });

    expect(getAttributeKeyNextCursor(terminal)).toBeUndefined();
    expect(
      getNextAttributeKeyPageParam(terminal, [terminal], null, [null]),
    ).toBeUndefined();
  });

  it("continues a bounded limit_reached page when its cursor advances", () => {
    const checkpoint = page([], {
      browse_status: "limit_reached",
      has_more: true,
      next_cursor: "next-bounded-batch",
    });

    expect(getAttributeKeyNextCursor(checkpoint)).toBe("next-bounded-batch");
    expect(
      getNextAttributeKeyPageParam(checkpoint, [checkpoint], null, [null]),
    ).toBe("next-bounded-batch");
  });

  it("stops a repeated cursor instead of looping or surfacing an error", async () => {
    const requestPage = vi
      .fn()
      .mockResolvedValueOnce(page([], { next_cursor: "same-cursor" }))
      .mockResolvedValueOnce(page([], { next_cursor: "same-cursor" }));

    const firstPage = await readAttributeKeyPage({
      pageParam: null,
      requestPage,
      signal: new AbortController().signal,
    });
    expect(isAttributeKeyCursorStopped(firstPage)).toBe(false);

    const result = await readAttributeKeyPage({
      pageParam: firstPage.next_cursor,
      publishedData: { pages: [firstPage], pageParams: [null] },
      requestPage,
      signal: new AbortController().signal,
    });

    expect(requestPage).toHaveBeenCalledTimes(2);
    expect(result.result).toEqual([]);
    expect(result.has_more).toBe(true);
    expect(result.next_cursor).toBe("same-cursor");
    expect(result).not.toHaveProperty("query_complete");
    expect(result).not.toHaveProperty("query_status");
    expect(result).not.toHaveProperty("query_sampled");
    expect(result).not.toHaveProperty("query_error_code");
    expect(isAttributeKeyCursorStopped(result)).toBe(true);
    expect(getAttributeKeyNextCursor(result)).toBeUndefined();
  });

  it("makes a malformed cursor degraded and retryable without claiming exhaustion", async () => {
    const result = await readAttributeKeyPage({
      pageParam: null,
      requestPage: vi.fn(() =>
        Promise.resolve(page(["recent.attribute"], { next_cursor: null })),
      ),
      signal: new AbortController().signal,
    });

    expect(result.result).toEqual([
      { key: "recent.attribute", type: "string" },
    ]);
    expect(result.browse_status).toBe("continuation");
    expect(result.has_more).toBe(true);
    expect(result.next_cursor).toBeNull();
    expect(result).not.toHaveProperty("query_complete");
    expect(result).not.toHaveProperty("query_status");
    expect(result).not.toHaveProperty("query_sampled");
    expect(result).not.toHaveProperty("query_error_code");
    expect(isAttributeKeyCursorStopped(result)).toBe(true);
  });

  it("preserves every cursor across arbitrarily many explicit gestures", async () => {
    const responseByCursor = new Map();
    responseByCursor.set(null, page([], { next_cursor: "checkpoint-1" }));
    for (let index = 1; index <= 14; index += 1) {
      responseByCursor.set(
        `checkpoint-${index}`,
        page([], { next_cursor: `checkpoint-${index + 1}` }),
      );
    }
    responseByCursor.set(
      "checkpoint-15",
      page(["eventual.attribute"], {
        browse_status: "exhausted",
        has_more: false,
        next_cursor: null,
      }),
    );
    const requestPage = vi.fn((cursor) =>
      Promise.resolve(responseByCursor.get(cursor ?? null)),
    );
    const publishedData = { pages: [], pageParams: [] };
    let cursor = null;
    let finalPage;
    for (let gesture = 0; gesture < 16; gesture += 1) {
      const currentCursor = cursor;
      finalPage = await readAttributeKeyPage({
        pageParam: currentCursor,
        publishedData,
        requestPage,
        signal: new AbortController().signal,
      });
      publishedData.pages.push(finalPage);
      publishedData.pageParams.push(currentCursor);
      cursor = getAttributeKeyNextCursor(finalPage);
      expect(requestPage).toHaveBeenCalledTimes(gesture + 1);
    }

    expect(finalPage.result).toEqual([
      { key: "eventual.attribute", type: "string" },
    ]);
    expect(finalPage.has_more).toBe(false);
    expect(requestPage).toHaveBeenCalledTimes(16);
    expect(requestPage.mock.calls.map(([pageCursor]) => pageCursor)).toEqual([
      null,
      ...Array.from({ length: 15 }, (_, index) => `checkpoint-${index + 1}`),
    ]);
    expect(
      publishedData.pages.every(
        (publishedPage) =>
          publishedPage.__attributeKeyFollowedCursors.length === 0,
      ),
    ).toBe(true);
  });

  it("does not de-duplicate a normal refetch against its unchanged old cache", async () => {
    const unchanged = page(["final_status"], {
      browse_status: "exhausted",
      has_more: false,
      next_cursor: null,
    });
    const result = await readAttributeKeyPage({
      pageParam: null,
      publishedData: {
        pages: [unchanged],
        pageParams: [null],
      },
      requestPage: vi.fn(() => Promise.resolve({ ...unchanged })),
      signal: new AbortController().signal,
    });

    expect(result.result).toEqual([{ key: "final_status", type: "string" }]);
    expect(result.browse_status).toBe("exhausted");
    expect(isAttributeKeyCursorStopped(result)).toBe(false);
  });

  it("keeps duplicate-heavy progress gap-free across explicit gestures", async () => {
    const published = page(["already.loaded"], {
      next_cursor: "load-more-start",
    });
    const requestPage = vi
      .fn()
      .mockResolvedValueOnce(
        page(["already.loaded", "new.one"], { next_cursor: "physical-2" }),
      )
      .mockResolvedValueOnce(
        page(["already.loaded", "new.two"], { next_cursor: "physical-3" }),
      )
      .mockResolvedValueOnce(
        page(["new.one", "new.three"], { next_cursor: "signed-final" }),
      );

    const firstPage = await readAttributeKeyPage({
      pageParam: "load-more-start",
      pageSize: 3,
      publishedData: {
        pages: [published],
        pageParams: [null],
      },
      requestPage,
      signal: new AbortController().signal,
    });
    expect(requestPage).toHaveBeenCalledTimes(1);
    expect(firstPage.result.map(({ key }) => key)).toEqual(["new.one"]);

    const secondPage = await readAttributeKeyPage({
      pageParam: firstPage.next_cursor,
      pageSize: 3,
      publishedData: {
        pages: [published, firstPage],
        pageParams: [null, "load-more-start"],
      },
      requestPage,
      signal: new AbortController().signal,
    });
    expect(requestPage).toHaveBeenCalledTimes(2);
    expect(secondPage.result.map(({ key }) => key)).toEqual(["new.two"]);

    const thirdPage = await readAttributeKeyPage({
      pageParam: secondPage.next_cursor,
      pageSize: 3,
      publishedData: {
        pages: [published, firstPage, secondPage],
        pageParams: [null, "load-more-start", firstPage.next_cursor],
      },
      requestPage,
      signal: new AbortController().signal,
    });

    expect(requestPage.mock.calls.map(([cursor]) => cursor)).toEqual([
      "load-more-start",
      "physical-2",
      "physical-3",
    ]);
    expect(thirdPage.result.map(({ key }) => key)).toEqual(["new.three"]);
    expect(thirdPage.next_cursor).toBe("signed-final");
    expect(
      [firstPage, secondPage, thirdPage].every(
        (result) => result.__attributeKeyFollowedCursors.length === 0,
      ),
    ).toBe(true);
  });

  it("does not re-request a cursor consumed inside the visible page", () => {
    const visiblePage = {
      ...page(["new.attribute"], { next_cursor: "outer-cursor" }),
      __attributeKeyFollowedCursors: ["internal-cursor"],
    };
    expect(
      getNextAttributeKeyPageParam(visiblePage, [visiblePage], null, [null]),
    ).toBe("outer-cursor");

    const repeatedPage = {
      ...visiblePage,
      next_cursor: "internal-cursor",
    };
    expect(
      getNextAttributeKeyPageParam(repeatedPage, [repeatedPage], null, [null]),
    ).toBeUndefined();
    expect(
      isAttributeKeyCursorChainStopped({
        pages: [repeatedPage],
        pageParams: [null],
      }),
    ).toBe(true);
  });

  it("marks a cursor repeated from an older chunk as retryable, not exhausted", () => {
    const firstPage = {
      ...page(["recent.attribute"], { next_cursor: "cursor-2" }),
      __attributeKeyFollowedCursors: ["cursor-1"],
    };
    const secondPage = {
      ...page(["middle.attribute"], { next_cursor: "cursor-3" }),
      __attributeKeyFollowedCursors: [],
    };
    const repeatedOlderCursorPage = {
      ...page(["older.attribute"], { next_cursor: "cursor-2" }),
      __attributeKeyFollowedCursors: [],
    };
    const data = {
      pages: [firstPage, secondPage, repeatedOlderCursorPage],
      pageParams: [null, "cursor-2", "cursor-3"],
    };

    expect(
      getNextAttributeKeyPageParam(
        repeatedOlderCursorPage,
        data.pages,
        "cursor-3",
        data.pageParams,
      ),
    ).toBeUndefined();
    expect(isAttributeKeyCursorChainStopped(data)).toBe(true);
  });
});
