import { describe, expect, it } from "vitest";

import {
  TracerObservationSpanListSpansObserveResponse,
  TracerObservationSpanListSpansResponse,
  TracerTraceAgentGraphResponse,
  TracerTraceListTracesOfSessionResponse,
  TracerTraceListTracesResponse,
  TracerTraceListVoiceCallsResponse,
  TracerTraceSessionListSessionsResponse,
} from "src/generated/api-contracts/api.zod";

const recursiveRow = {
  id: "trace-1",
  latency_ms: 12.5,
  successful: true,
  nullable_cell: null,
  tags: ["prod", 2, false, null, { nested: ["value"] }],
  metadata: { final_status: "Rechazado", score: 0.8 },
};

const column = {
  id: "metadata",
  name: "Metadata",
  is_visible: true,
  group_by: null,
  output_type: null,
  reverse_output: null,
  annotation_label_type: null,
  choices: [null, "Rechazado"],
  settings: { nested: [1, true, null] },
  choices_map: { Rechazado: { label: "Rejected" } },
  eval_template_id: null,
  annotators: ["reviewer-1"],
  source_field: null,
  parent_eval_id: null,
};

const metadata = {
  total_rows: 1,
  total_rows_exact: null,
  total_rows_is_lower_bound: false,
  has_more: false,
  next_cursor: null,
  query_complete: true,
  query_status: "complete",
  query_error_code: null,
};

describe("generated Observe response contracts", () => {
  it.each([
    [
      "prototype traces",
      TracerTraceListTracesResponse,
      {
        status: true,
        result: { column_config: [column], metadata, table: [recursiveRow] },
      },
    ],
    [
      "Observe traces",
      TracerTraceListTracesOfSessionResponse,
      {
        status: true,
        result: { config: [column], metadata, table: [recursiveRow] },
      },
    ],
    [
      "Observe sessions",
      TracerTraceSessionListSessionsResponse,
      {
        status: true,
        result: { config: [column], metadata, table: [recursiveRow] },
      },
    ],
    [
      "prototype spans",
      TracerObservationSpanListSpansResponse,
      {
        status: true,
        result: { column_config: [column], metadata, table: [recursiveRow] },
      },
    ],
    [
      "Observe spans",
      TracerObservationSpanListSpansObserveResponse,
      {
        status: true,
        result: { config: [column], metadata, table: [recursiveRow] },
      },
    ],
  ])(
    "accepts %s rows with every recursive JSON value",
    (_name, schema, payload) => {
      expect(schema.safeParse(payload)).toMatchObject({ success: true });
    },
  );

  it("rejects non-JSON dynamic row cells", () => {
    const payload = {
      status: true,
      result: {
        config: [column],
        metadata,
        table: [{ ...recursiveRow, unsupported: undefined }],
      },
    };

    expect(
      TracerTraceListTracesOfSessionResponse.safeParse(payload),
    ).toMatchObject({ success: false });
  });

  it("accepts both pending and complete exact agent-graph snapshots", () => {
    expect(
      TracerTraceAgentGraphResponse.safeParse({
        status: true,
        result: {
          nodes: [],
          edges: [],
          path_edges: [],
          query_complete: false,
          query_status: "pending",
          query_refreshing: true,
        },
      }),
    ).toMatchObject({ success: true });

    expect(
      TracerTraceAgentGraphResponse.safeParse({
        status: true,
        result: {
          nodes: [
            {
              id: "agent",
              name: "Agent",
              type: "agent",
              span_count: 2,
              avg_latency_ms: 15,
              total_tokens: 4,
              total_cost: 0.01,
              error_count: 0,
              trace_count: null,
            },
          ],
          edges: [
            {
              source: "agent",
              target: "tool",
              transition_count: 1,
              avg_latency_ms: 10,
              total_tokens: 0,
              total_cost: 0,
              error_count: 0,
              trace_count: null,
              is_self_loop: false,
            },
          ],
          path_edges: [],
          query_complete: true,
          query_status: "complete",
          query_sampled: false,
        },
      }),
    ).toMatchObject({ success: true });
  });

  it("rejects malformed agent graph nodes", () => {
    expect(
      TracerTraceAgentGraphResponse.safeParse({
        status: true,
        result: {
          nodes: [{ id: "agent" }],
          edges: [],
          path_edges: [],
        },
      }),
    ).toMatchObject({ success: false });
  });

  it("accepts voice-call pagination and recursive JSON results", () => {
    expect(
      TracerTraceListVoiceCallsResponse.safeParse({
        count: 1,
        count_is_lower_bound: false,
        total_pages: 1,
        current_page: 1,
        next: null,
        previous: null,
        results: [recursiveRow],
        config: [column],
        has_more: false,
        next_cursor: null,
        query_complete: true,
        query_status: "complete",
      }),
    ).toMatchObject({ success: true });
  });
});
