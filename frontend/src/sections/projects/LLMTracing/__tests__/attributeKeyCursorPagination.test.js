import { describe, expect, it, vi } from "vitest";

import {
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
  it("drains advancing empty checkpoints into one visible page", async () => {
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

    const result = await readAttributeKeyPage({
      pageParam: null,
      requestPage,
      signal: new AbortController().signal,
    });

    expect(requestPage.mock.calls.map(([cursor]) => cursor)).toEqual([
      null,
      "checkpoint-1",
      "checkpoint-2",
    ]);
    expect(result.result).toEqual([{ key: "older.attribute", type: "string" }]);
    expect(result.has_more).toBe(false);
    expect(getAttributeKeyNextCursor(result)).toBeUndefined();
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

    const result = await readAttributeKeyPage({
      pageParam: null,
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

  it("preserves progress across bounded chunks longer than twelve checkpoints", async () => {
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
    const firstChunk = await readAttributeKeyPage({
      pageParam: null,
      requestPage,
      signal: new AbortController().signal,
    });
    expect(firstChunk.result).toEqual([]);
    expect(firstChunk.has_more).toBe(true);
    expect(firstChunk.next_cursor).toBe("checkpoint-13");

    const secondChunk = await readAttributeKeyPage({
      pageParam: firstChunk.next_cursor,
      requestPage,
      signal: new AbortController().signal,
    });

    expect(secondChunk.result).toEqual([
      { key: "eventual.attribute", type: "string" },
    ]);
    expect(secondChunk.has_more).toBe(false);
    expect(requestPage).toHaveBeenCalledTimes(16);
    expect(firstChunk.__attributeKeyFollowedCursors).toHaveLength(12);
    expect(secondChunk.__attributeKeyFollowedCursors).toEqual([
      "checkpoint-14",
      "checkpoint-15",
    ]);
  });

  it("does not de-duplicate a normal refetch against its unchanged old cache", async () => {
    const unchanged = page(["final_status"], {
      browse_status: "exhausted",
      has_more: false,
      next_cursor: null,
    });
    const result = await readAttributeKeyPage({
      pageParam: null,
      requestPage: vi.fn(() => Promise.resolve({ ...unchanged })),
      signal: new AbortController().signal,
    });

    expect(result.result).toEqual([{ key: "final_status", type: "string" }]);
    expect(result.browse_status).toBe("exhausted");
    expect(isAttributeKeyCursorStopped(result)).toBe(false);
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
