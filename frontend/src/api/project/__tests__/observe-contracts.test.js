import { describe, expect, it } from "vitest";
import {
  parseAxiosResult,
  parseSpanObserveListResponse,
  parseTraceGraphResponse,
  parseTraceObserveListResponse,
  parseVoiceCallDetailResponse,
  parseVoiceCallListResponse,
} from "../observe-contracts";

const emptyObserveListBody = {
  status: true,
  result: {
    metadata: {
      total_rows: 0,
      has_more: false,
      next_cursor: null,
    },
    table: [],
    config: [],
  },
};

describe("Observe API response contracts", () => {
  it.each([
    ["trace", parseTraceObserveListResponse],
    ["span", parseSpanObserveListResponse],
  ])("returns only the canonical %s-list result", (_label, parse) => {
    expect(parse(emptyObserveListBody)).toEqual(emptyObserveListBody.result);
    expect(() => parse({ data: emptyObserveListBody })).toThrow();
    expect(() => parse({ result: emptyObserveListBody.result })).toThrow();
  });

  it("requires the generated graph wrapper and returns its sole result shape", () => {
    const body = {
      status: true,
      result: {
        metric_name: "latency",
        data: [],
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
      },
    };

    expect(parseTraceGraphResponse(body)).toEqual(body.result);
    expect(() => parseTraceGraphResponse(body.result)).toThrow();
  });

  it("accepts exact graph gaps represented by nullable values", () => {
    const body = {
      status: true,
      result: {
        metric_name: "latency",
        data: [
          {
            timestamp: "2026-08-09T00:00:00Z",
            value: null,
            primary_traffic: null,
          },
        ],
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
      },
    };

    expect(parseTraceGraphResponse(body)).toEqual(body.result);
  });

  it("keeps voice-call list as the declared direct-body exception", () => {
    const body = {
      count: 0,
      count_is_lower_bound: false,
      total_pages: 0,
      current_page: 1,
      next: null,
      previous: null,
      results: [],
      config: [],
      has_more: false,
      next_cursor: null,
      query_complete: true,
      query_status: "complete",
    };

    expect(parseVoiceCallListResponse(body)).toEqual(body);
    expect(() => parseVoiceCallListResponse({ result: body })).toThrow();
  });

  it("requires the voice-detail success envelope", () => {
    const body = {
      status: true,
      result: {
        id: "trace-1",
        trace_id: "trace-1",
        project_id: "project-1",
        provider_call_id: null,
        phone_number: null,
        duration_seconds: null,
        cost_breakdown: null,
        transcript: [{ role: "user", content: "hello" }],
        messages: null,
        analysis_data: null,
        scenario_id: null,
        recording: {},
        recording_available: false,
        call_metadata: {},
        observation_span: [],
        eval_outputs: {},
        turn_count: 1,
        talk_ratio: null,
        agent_talk_percentage: null,
        bot_talk_pct: null,
        user_talk_pct: null,
        avg_agent_latency_ms: null,
        user_wpm: null,
        bot_wpm: null,
        user_interruption_count: null,
        ai_interruption_count: null,
      },
    };

    expect(parseVoiceCallDetailResponse(body)).toEqual(body.result);
    expect(() => parseVoiceCallDetailResponse(body.result)).toThrow();
    expect(() =>
      parseVoiceCallDetailResponse({
        count: 1,
        results: [body.result],
      }),
    ).toThrow();
  });

  it("preserves Axios transport metadata while canonicalizing the body", () => {
    expect(
      parseAxiosResult(
        { data: emptyObserveListBody, status: 200, headers: { etag: "v1" } },
        parseTraceObserveListResponse,
      ),
    ).toEqual({
      data: emptyObserveListBody.result,
      status: 200,
      headers: { etag: "v1" },
    });
  });
});
