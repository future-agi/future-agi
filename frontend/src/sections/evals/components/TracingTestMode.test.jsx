import { describe, expect, it } from "vitest";

import {
  buildTracingPreviewListParams,
  findTracingRowIndexByTraceId,
  getTracingRowIdentifiers,
} from "./TracingTestMode";

describe("buildTracingPreviewListParams", () => {
  it("does not send unsupported interval params to observe list endpoints", () => {
    const params = buildTracingPreviewListParams({
      selectedProjectId: "project-1",
      effectiveFilters: [
        {
          column_id: "created_at",
          filter_config: {
            filter_type: "datetime",
            filter_op: "between",
            filter_value: [
              "2025-01-01T00:00:00.000Z",
              "2026-01-01T00:00:00.000Z",
            ],
          },
        },
      ],
    });

    expect(params).toEqual({
      project_id: "project-1",
      page_number: 0,
      page_size: 50,
      filters: JSON.stringify([
        {
          column_id: "created_at",
          filter_config: {
            filter_type: "datetime",
            filter_op: "between",
            filter_value: [
              "2025-01-01T00:00:00.000Z",
              "2026-01-01T00:00:00.000Z",
            ],
          },
        },
      ]),
    });
    expect(params).not.toHaveProperty("interval");
  });
});

describe("getTracingRowIdentifiers", () => {
  it("uses the trace table id as trace_id fallback for trace rows", () => {
    expect(
      getTracingRowIdentifiers(
        {
          id: "trace-1",
          name: "First trace",
        },
        "Trace",
      ),
    ).toEqual({
      spanId: null,
      traceId: "trace-1",
      sessionId: null,
    });
  });

  it("uses explicit ids before row id fallbacks", () => {
    expect(
      getTracingRowIdentifiers(
        {
          id: "row-1",
          span_id: "span-1",
          trace_id: "trace-1",
          session_id: "session-1",
        },
        "Span",
      ),
    ).toEqual({
      spanId: "span-1",
      traceId: "trace-1",
      sessionId: "session-1",
    });
  });
});

describe("findTracingRowIndexByTraceId", () => {
  it("finds the requested initial trace row", () => {
    expect(
      findTracingRowIndexByTraceId(
        [
          { id: "trace-0", trace_id: "trace-0" },
          { id: "row-1", trace_id: "trace-1" },
        ],
        "trace-1",
      ),
    ).toBe(1);
  });

  it("returns -1 when the requested trace is not loaded", () => {
    expect(findTracingRowIndexByTraceId([{ id: "trace-0" }], "trace-1")).toBe(
      -1,
    );
  });
});
