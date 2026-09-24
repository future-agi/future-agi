import { describe, expect, it } from "vitest";

import {
  parsePrototypeSpanListResponse,
  parsePrototypeTraceListResponse,
} from "../telemetry-list-contract";

const columnConfig = [
  {
    id: "trace_name",
    name: "Trace name",
    is_visible: true,
    group_by: "system",
    output_type: "text",
    source_field: "trace_name",
  },
];

describe("generated prototype telemetry list contracts", () => {
  it("projects a trace page containing mixed recursive JSON values", () => {
    const row = {
      trace_id: "trace-1",
      latency: 12,
      session_id: null,
      tags: ["one"],
      metadata: { nested: true },
    };
    const result = parsePrototypeTraceListResponse({
      status: true,
      result: {
        column_config: columnConfig,
        metadata: { total_rows: 41, has_more: true },
        table: [row],
      },
    });

    expect(result.table).toEqual([row]);
    expect(result.totalRows).toBe(41);
    expect(result.hasMore).toBe(true);
    expect(result.columnConfig[0]).toEqual(
      expect.objectContaining({
        isVisible: true,
        groupBy: "system",
        outputType: "text",
        sourceField: "trace_name",
      }),
    );
  });

  it("validates the distinct span endpoint and its row identity", () => {
    const first = {
      project_id: "project-a",
      trace_id: "trace-a",
      span_id: "span-1",
      start_time: "2026-08-09T00:00:00Z",
      latency_ms: 4,
    };
    const second = {
      project_id: "project-a",
      trace_id: "trace-b",
      span_id: "span-1",
      start_time: "2026-08-09T00:01:00Z",
      latency_ms: 5,
    };
    const result = parsePrototypeSpanListResponse({
      status: true,
      result: {
        column_config: columnConfig,
        metadata: { total_rows: 2, has_more: false },
        table: [first, second],
      },
    });

    expect(result.table).toEqual([first, second]);
    expect(result.hasMore).toBe(false);
  });

  it.each([
    {
      project_id: "project-a",
      span_id: "span-1",
      start_time: "2026-08-09T00:00:00Z",
    },
    {
      project_id: "project-a",
      trace_id: "trace-a",
      start_time: "2026-08-09T00:00:00Z",
    },
    { project_id: "project-a", trace_id: "trace-a", span_id: "span-1" },
    {
      trace_id: "trace-a",
      span_id: "span-1",
      start_time: "2026-08-09T00:00:00Z",
    },
  ])("rejects a span row missing part of its physical identity", (row) => {
    expect(() =>
      parsePrototypeSpanListResponse({
        status: true,
        result: {
          column_config: columnConfig,
          metadata: { total_rows: 1, has_more: false },
          table: [row],
        },
      }),
    ).toThrow("missing canonical span physical identity");
  });

  it("distinguishes an omitted continuation field from a terminal page", () => {
    const result = parsePrototypeTraceListResponse({
      status: true,
      result: {
        column_config: [],
        metadata: { total_rows: 1 },
        table: [{ trace_id: "trace-1" }],
      },
    });

    expect(result.hasMore).toBeNull();
  });

  it.each([
    ["missing envelope", undefined],
    [
      "missing status",
      { result: { column_config: [], metadata: { total_rows: 0 }, table: [] } },
    ],
    [
      "unsuccessful status",
      {
        status: false,
        result: { column_config: [], metadata: { total_rows: 0 }, table: [] },
      },
    ],
    ["missing result", { status: true }],
    [
      "Observe config in place of prototype column_config",
      {
        status: true,
        result: { config: [], metadata: { total_rows: 0 }, table: [] },
      },
    ],
    [
      "missing trace identity",
      {
        status: true,
        result: {
          column_config: [],
          metadata: { total_rows: 1 },
          table: [{ id: "not-a-trace-id" }],
        },
      },
    ],
  ])("rejects %s instead of manufacturing an empty page", (_, payload) => {
    expect(() => parsePrototypeTraceListResponse(payload)).toThrow();
  });
});
