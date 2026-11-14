import { describe, expect, it, vi } from "vitest";

import { OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS } from "src/config/runtime_limits";
import {
  boundObserveListRow,
  compactObserveListResponse,
} from "../observeListPayload";

describe("boundObserveListRow", () => {
  it("preserves ordinary values and bounds large prompts", () => {
    const tags = ["one", "two"];
    const row = boundObserveListRow({
      trace_id: "trace-1",
      tags,
      input: "x".repeat(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS + 50),
    });

    expect(row.trace_id).toBe("trace-1");
    expect(row.tags).toBe(tags);
    expect(row.input).toHaveLength(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS);
    expect(row.input).toMatch(/…$/);
  });

  it("serializes only structured values that exceed the preview budget", () => {
    const small = { customer: "a" };
    const row = boundObserveListRow({
      small,
      huge: { payload: "x".repeat(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS + 50) },
    });

    expect(row.small).toBe(small);
    expect(typeof row.huge).toBe("string");
    expect(row.huge).toHaveLength(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS);
    expect(row.huge).toMatch(/…$/);
  });

  it("never materializes an unbounded structured JSON string before truncation", () => {
    const stringify = vi.spyOn(JSON, "stringify");
    const hugeValue = "x".repeat(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS * 100);

    const row = boundObserveListRow({ metadata: { payload: hugeValue } });

    expect(row.metadata).toHaveLength(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS);
    expect(row.metadata).toMatch(/…$/);
    expect(stringify).not.toHaveBeenCalledWith(
      expect.objectContaining({ payload: hugeValue }),
    );
    expect(
      stringify.mock.calls.every(
        ([value]) =>
          typeof value !== "string" ||
          value.length <= OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS + 1,
      ),
    ).toBe(true);
    stringify.mockRestore();
  });

  it("bounds circular structured values without throwing", () => {
    const circular = { id: "trace-1" };
    circular.self = circular;
    circular.payload = "x".repeat(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS * 2);

    const row = boundObserveListRow({ metadata: circular });

    expect(row.metadata).toHaveLength(OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS);
    expect(row.metadata).toMatch(/…$/);
  });
});

describe("compactObserveListResponse", () => {
  it("drops transport objects and raw rows while preserving list metadata", () => {
    const request = { responseText: "x".repeat(10_000) };
    const response = {
      data: {
        table: [{ trace_id: "trace-1", metadata: "large" }],
        config: [{ id: "trace_id" }],
        metadata: { next_cursor: "cursor-2", has_more: true },
        query_status: "complete",
        query_complete: true,
      },
      request,
      config: { url: "/api/traces" },
      headers: { "content-length": "10000" },
    };

    const compact = compactObserveListResponse(response);

    expect(compact).toEqual({
      data: {
        table: [],
        config: [{ id: "trace_id" }],
        metadata: { next_cursor: "cursor-2", has_more: true },
        query_status: "complete",
        query_complete: true,
      },
    });
    expect(compact).not.toHaveProperty("request");
    expect(compact).not.toHaveProperty("config");
    expect(compact).not.toHaveProperty("headers");
  });

  it("drops nested result rows used by session and user list endpoints", () => {
    const response = {
      data: {
        status: true,
        result: {
          table: [{ session_id: "session-1", metadata: "x".repeat(10_000) }],
          config: [{ id: "session_id" }],
          metadata: { has_more: true, next_cursor: "cursor-2" },
        },
      },
      request: { responseText: "x".repeat(20_000) },
    };

    expect(compactObserveListResponse(response)).toEqual({
      data: {
        status: true,
        result: {
          table: [],
          config: [{ id: "session_id" }],
          metadata: { has_more: true, next_cursor: "cursor-2" },
        },
      },
    });
  });
});
