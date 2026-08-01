import { describe, expect, it, vi } from "vitest";

import {
  buildTracingPreviewListParams,
  fetchExactSpanPreview,
  fetchTracingPreviewList,
  getTracingReadyState,
  isTracingListQueryDegraded,
  resolveExactSpanDetail,
} from "./tracingTestModeUtils";

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
      preview: true,
    });
    expect(params).not.toHaveProperty("interval");
  });
});

describe("isTracingListQueryDegraded", () => {
  it.each([
    { query_complete: false },
    { query_status: "degraded" },
    { query_error_code: "read_budget_exceeded" },
  ])("recognizes an incomplete list response: %o", (metadata) => {
    expect(isTracingListQueryDegraded(metadata)).toBe(true);
  });

  it("keeps a complete empty result distinct from a degraded result", () => {
    expect(
      isTracingListQueryDegraded({
        query_complete: true,
        query_status: "complete",
        total_rows: 0,
      }),
    ).toBe(false);
  });
});

describe("fetchTracingPreviewList", () => {
  it("accepts a valid complete list response", async () => {
    const httpGet = vi.fn().mockResolvedValue({
      data: {
        result: {
          config: [{ id: "span_id" }],
          table: [{ span_id: "span-1" }],
          metadata: { total_rows: 1 },
        },
      },
    });

    await expect(
      fetchTracingPreviewList({
        httpGet,
        endpoint: "/tracer/observation-span/list_spans_observe/",
        params: { project_id: "project-1", preview: true },
      }),
    ).resolves.toEqual({
      columns: [{ id: "span_id" }],
      rows: [{ span_id: "span-1" }],
      totalRows: 1,
      queryDegraded: false,
      queryUnavailable: false,
    });
  });

  it("fails closed without exposing backend details when the list request rejects", async () => {
    const httpGet = vi
      .fn()
      .mockRejectedValue(
        new Error("Code: 159. DB::Exception: private ClickHouse stack"),
      );

    const result = await fetchTracingPreviewList({
      httpGet,
      endpoint: "/tracer/observation-span/list_spans_observe/",
      params: { project_id: "project-1", preview: true },
    });

    expect(result).toEqual({
      columns: [],
      rows: [],
      totalRows: 0,
      queryDegraded: false,
      queryUnavailable: true,
    });
    expect(JSON.stringify(result)).not.toMatch(/DB::Exception/i);
  });

  it.each([
    ["missing result", { data: { status: true } }],
    [
      "non-array table",
      {
        data: {
          result: {
            config: [],
            table: {},
            metadata: { total_rows: 0 },
          },
        },
      },
    ],
    [
      "non-array config",
      {
        data: {
          result: {
            config: {},
            table: [],
            metadata: { total_rows: 0 },
          },
        },
      },
    ],
    [
      "non-object metadata",
      {
        data: {
          result: {
            config: [],
            table: [],
            metadata: [],
          },
        },
      },
    ],
    [
      "invalid total",
      {
        data: {
          result: {
            config: [],
            table: [],
            metadata: { total_rows: -1 },
          },
        },
      },
    ],
  ])(
    "fails closed for a fulfilled malformed response: %s",
    async (_, response) => {
      const result = await fetchTracingPreviewList({
        httpGet: vi.fn().mockResolvedValue(response),
        endpoint: "/tracer/observation-span/list_spans_observe/",
        params: { project_id: "project-1", preview: true },
      });

      expect(result).toEqual({
        columns: [],
        rows: [],
        totalRows: 0,
        queryDegraded: false,
        queryUnavailable: true,
      });
    },
  );
});

describe("resolveExactSpanDetail", () => {
  const spanResult = {
    observation_span: {
      id: "span-selected",
      span_attributes: { prompt_slug: "expected" },
    },
  };

  it("returns the exact selected span from the point-detail response", () => {
    expect(resolveExactSpanDetail(spanResult, "span-selected")).toEqual({
      detail: {
        id: "span-selected",
        span_attributes: { prompt_slug: "expected" },
      },
      error: null,
    });
  });

  it("returns no detail when the point response does not match the row", () => {
    const result = resolveExactSpanDetail(spanResult, "span-stale");

    expect(result.detail).toBeNull();
    expect(result.error).toMatch(/no longer available/i);
  });
});

describe("fetchExactSpanPreview", () => {
  it("hydrates only the selected span through the bounded preview endpoint", async () => {
    const calls = [];
    const result = await fetchExactSpanPreview({
      spanId: "span-selected",
      getObservationSpanUrl: (id) => `/tracer/observation-span/${id}/`,
      httpGet: async (...args) => {
        calls.push(args);
        return {
          data: {
            result: {
              observation_span: {
                id: "span-selected",
                span_attributes: { prompt_slug: "expected" },
              },
            },
          },
        };
      },
    });

    expect(calls).toEqual([
      [
        "/tracer/observation-span/span-selected/",
        { params: { preview: true } },
      ],
    ]);
    expect(result.detail?.id).toBe("span-selected");
  });
});

describe("getTracingReadyState", () => {
  it("returns no mapping and blocks readiness for a stale span row", () => {
    expect(
      getTracingReadyState({
        variables: ["transcript"],
        mapping: { transcript: "root_only" },
        currentRow: { span_id: "span-stale", trace_id: "trace-1" },
        spanDetail: null,
        rowType: "spans",
        hasDataInjection: true,
        spanDetailError: "The selected span is stale",
      }),
    ).toEqual({ ready: false, mapping: {} });
  });

  it("allows a resolved exact span with a complete mapping", () => {
    const mapping = { transcript: "span_attributes.transcript" };
    expect(
      getTracingReadyState({
        variables: ["transcript"],
        mapping,
        currentRow: { span_id: "span-selected", trace_id: "trace-1" },
        spanDetail: { id: "span-selected" },
        rowType: "Span",
        hasDataInjection: false,
        spanDetailError: null,
      }),
    ).toEqual({ ready: true, mapping });
  });

  it("does not treat detail from the previous span as resolved", () => {
    expect(
      getTracingReadyState({
        variables: [],
        mapping: {},
        currentRow: { span_id: "span-selected", trace_id: "trace-1" },
        spanDetail: { id: "span-previous" },
        rowType: "Span",
        hasDataInjection: false,
        spanDetailError: null,
      }),
    ).toEqual({ ready: false, mapping: {} });
  });

  it("blocks a selected span while exact hydration is pending even with data injection", () => {
    expect(
      getTracingReadyState({
        variables: [],
        mapping: {},
        currentRow: { span_id: "span-selected", trace_id: "trace-1" },
        spanDetail: null,
        rowType: "Span",
        hasDataInjection: true,
        spanDetailError: null,
      }),
    ).toEqual({ ready: false, mapping: {} });
  });

  it("blocks readiness and clears mapping for an incomplete list response", () => {
    expect(
      getTracingReadyState({
        variables: ["transcript"],
        mapping: { transcript: "span_attributes.transcript" },
        currentRow: { span_id: "span-selected", trace_id: "trace-1" },
        spanDetail: { id: "span-selected" },
        rowType: "Span",
        hasDataInjection: true,
        spanDetailError: null,
        listQueryDegraded: true,
      }),
    ).toEqual({ ready: false, mapping: {} });
  });

  it("blocks readiness and clears mapping for an unavailable list even with data injection", () => {
    expect(
      getTracingReadyState({
        variables: ["transcript"],
        mapping: { transcript: "span_attributes.transcript" },
        currentRow: null,
        spanDetail: null,
        rowType: "Span",
        hasDataInjection: true,
        spanDetailError: null,
        listQueryUnavailable: true,
      }),
    ).toEqual({ ready: false, mapping: {} });
  });
});
