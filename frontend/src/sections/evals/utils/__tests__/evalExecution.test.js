import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildAutoCtx,
  buildCompositeCtx,
  executeEvalForRow,
  normalizeRowType,
  resolveMappingFromRow,
} from "../evalExecution";

// We mock the axios *module* used by the helper. The helper imports the
// default export and pushes `endpoints` through the same module, so both
// `axios.post` and `endpoints.develop.eval.*` resolve to the mock.
vi.mock("src/utils/axios", () => {
  const post = vi.fn();
  return {
    default: { post },
    endpoints: {
      develop: {
        eval: {
          evalPlayground: "/model-hub/eval-playground/",
          executeCompositeEval: (id) =>
            `/model-hub/eval-templates/${id}/composite/execute/`,
          executeCompositeEvalAdhoc:
            "/model-hub/eval-templates/composite/execute-adhoc/",
        },
      },
    },
  };
});

import axios from "src/utils/axios";

const POST = axios.post;

beforeEach(() => {
  POST.mockReset();
});

describe("normalizeRowType", () => {
  it.each([
    ["Span", "Span"],
    ["spans", "Span"],
    ["TRACE", "Trace"],
    ["traces", "Trace"],
    ["session", "Session"],
    ["sessions", "Session"],
    ["voicecall", "VoiceCall"],
    ["voice_calls", "VoiceCall"],
    ["voiceCalls", "VoiceCall"],
    [undefined, "Span"],
    [null, "Span"],
    ["nonsense", "Span"],
  ])("normalizes %p -> %p", (input, expected) => {
    expect(normalizeRowType(input)).toBe(expected);
  });
});

describe("buildAutoCtx", () => {
  const spanRow = { span_id: "s1", trace_id: "t1", session_id: null };
  it("sends span_id + trace_id for Span", () => {
    expect(buildAutoCtx({ rowType: "Span", currentRow: spanRow })).toEqual({
      span_id: "s1",
      trace_id: "t1",
    });
  });
  it("sends only trace_id for Trace", () => {
    expect(buildAutoCtx({ rowType: "Trace", currentRow: spanRow })).toEqual({
      trace_id: "t1",
    });
  });
  it("sends only session_id for Session", () => {
    expect(
      buildAutoCtx({
        rowType: "Session",
        currentRow: { session_id: "sess1" },
      }),
    ).toEqual({ session_id: "sess1" });
  });
  it("sends trace_id for VoiceCall", () => {
    expect(
      buildAutoCtx({ rowType: "VoiceCall", currentRow: { trace_id: "t1" } }),
    ).toEqual({ trace_id: "t1" });
  });
  it("returns empty when currentRow is null", () => {
    expect(buildAutoCtx({ rowType: "Span", currentRow: null })).toEqual({});
  });
});

describe("buildCompositeCtx", () => {
  it("sends span_context = spanDetail for Span", () => {
    const spanDetail = { foo: "bar" };
    const currentRow = { span_id: "s1" };
    expect(
      buildCompositeCtx({ rowType: "Span", currentRow, spanDetail }),
    ).toEqual({ span_context: spanDetail });
  });
  it("sends trace_context = currentRow for Trace", () => {
    const row = { trace_id: "t1" };
    expect(
      buildCompositeCtx({ rowType: "Trace", currentRow: row, spanDetail: {} }),
    ).toEqual({ trace_context: row });
  });
  it("sends session_context = currentRow for Session", () => {
    const row = { session_id: "sess1" };
    expect(
      buildCompositeCtx({
        rowType: "Session",
        currentRow: row,
        spanDetail: null,
      }),
    ).toEqual({ session_context: row });
  });
  it("sends trace_context = currentRow for VoiceCall", () => {
    const row = { trace_id: "t1" };
    expect(
      buildCompositeCtx({
        rowType: "VoiceCall",
        currentRow: row,
        spanDetail: null,
      }),
    ).toEqual({ trace_context: row });
  });
});

describe("resolveMappingFromRow", () => {
  const spanDetail = {
    span_attributes: { input: { value: "hello" }, cost: 2 },
    name: "root-span",
  };

  it("resolves mapped variables via per-path walk (soft-flattened)", () => {
    const out = resolveMappingFromRow(
      { query: "input.value", label: "name" },
      spanDetail,
    );
    expect(out).toEqual({ query: "hello", label: "root-span" });
  });

  it("stringifies object values and skips unresolved/missing fields", () => {
    const out = resolveMappingFromRow(
      { blob: "input", gone: "does.not.exist", empty: "" },
      spanDetail,
    );
    expect(out).toEqual({ blob: JSON.stringify({ value: "hello" }) });
  });

  it("falls back to rowFields for non-detail columns", () => {
    const out = resolveMappingFromRow({ note: "annot_col" }, spanDetail, [
      { key: "annot_col", raw: "from-row" },
    ]);
    expect(out).toEqual({ note: "from-row" });
  });
});

describe("executeEvalForRow — single eval", () => {
  it("posts to /eval-playground/ with autoCtx + resolved mapping for Span", async () => {
    POST.mockResolvedValueOnce({
      data: { status: true, result: { score: 1, log_id: "log-1" } },
    });
    const result = await executeEvalForRow({
      evalItem: { template_id: "tpl-1", model: "turing_large" },
      rowType: "Span",
      currentRow: { span_id: "s1", trace_id: "t1" },
      spanDetail: { input: { value: "hi" } },
      mapping: { question: "input.value" },
    });
    expect(POST).toHaveBeenCalledWith("/model-hub/eval-playground/", {
      template_id: "tpl-1",
      model: "turing_large",
      error_localizer: false,
      config: { mapping: { question: "hi" } },
      span_id: "s1",
      trace_id: "t1",
    });
    expect(result).toMatchObject({
      ok: true,
      isComposite: false,
      logId: "log-1",
    });
  });

  it("uses mapping_paths and omits config.mapping for Session", async () => {
    POST.mockResolvedValueOnce({
      data: { status: true, result: { score: 0.5 } },
    });
    await executeEvalForRow({
      evalItem: { template_id: "tpl-1" },
      rowType: "Session",
      currentRow: { session_id: "sess1" },
      spanDetail: null,
      mapping: { input: "user_message_text" },
      singleEvalConfigExtras: {
        run_config: { data_injection: { messages: true } },
      },
    });
    expect(POST).toHaveBeenCalledWith("/model-hub/eval-playground/", {
      template_id: "tpl-1",
      model: "turing_large",
      error_localizer: false,
      config: { run_config: { data_injection: { messages: true } } },
      mapping_paths: { input: "user_message_text" },
      session_id: "sess1",
    });
  });

  it("returns ok:false with errorMessage on non-status response", async () => {
    POST.mockResolvedValueOnce({
      data: { status: false, result: "boom" },
    });
    const result = await executeEvalForRow({
      evalItem: { template_id: "tpl-1" },
      rowType: "Span",
      currentRow: { span_id: "s1" },
      spanDetail: {},
      mapping: {},
    });
    expect(result).toMatchObject({ ok: false, errorMessage: "boom" });
  });

  it("catches axios throw and returns ok:false", async () => {
    POST.mockRejectedValueOnce({ message: "network down" });
    const result = await executeEvalForRow({
      evalItem: { template_id: "tpl-1" },
      rowType: "Span",
      currentRow: { span_id: "s1" },
      spanDetail: {},
      mapping: {},
    });
    expect(result).toMatchObject({ ok: false, errorMessage: "network down" });
  });
});

describe("executeEvalForRow — composite", () => {
  it("posts to /composite/execute/{id}/ with span_context for saved composite", async () => {
    POST.mockResolvedValueOnce({
      data: {
        status: true,
        result: {
          aggregation_enabled: true,
          aggregate_score: 0.8,
          summary: "looks good",
        },
      },
    });
    const spanDetail = { foo: "bar" };
    const result = await executeEvalForRow({
      evalItem: {
        template_id: "tpl-c",
        template_type: "composite",
        model: "turing_large",
      },
      rowType: "Span",
      currentRow: { span_id: "s1" },
      spanDetail,
      mapping: {},
    });
    expect(POST).toHaveBeenCalledWith(
      "/model-hub/eval-templates/tpl-c/composite/execute/",
      {
        mapping: {},
        model: "turing_large",
        error_localizer: false,
        config: {},
        span_context: spanDetail,
      },
    );
    expect(result).toMatchObject({
      ok: true,
      isComposite: true,
      output: 0.8,
      reason: "looks good",
    });
    expect(result.compositeResult).toEqual({
      aggregation_enabled: true,
      aggregate_score: 0.8,
      summary: "looks good",
    });
  });

  it("posts to /composite/execute-adhoc/ when compositeAdhocConfig is provided", async () => {
    POST.mockResolvedValueOnce({
      data: {
        status: true,
        result: { aggregation_enabled: false, summary: "n/a" },
      },
    });
    const compositeAdhocConfig = {
      child_template_ids: ["a", "b"],
      child_configs: {},
      aggregation_enabled: false,
      pass_threshold: 0.5,
    };
    await executeEvalForRow({
      evalItem: { model: "turing_large" }, // no template_type — adhoc forces composite
      rowType: "Trace",
      currentRow: { trace_id: "t1" },
      spanDetail: {},
      mapping: {},
      compositeAdhocConfig,
    });
    expect(POST).toHaveBeenCalledWith(
      "/model-hub/eval-templates/composite/execute-adhoc/",
      expect.objectContaining({
        ...compositeAdhocConfig,
        model: "turing_large",
        error_localizer: false,
        config: {},
        trace_context: { trace_id: "t1" },
      }),
    );
  });

  it("aggregate_score is null when aggregation is disabled", async () => {
    POST.mockResolvedValueOnce({
      data: {
        status: true,
        result: {
          aggregation_enabled: false,
          aggregate_score: 0.4,
          summary: "",
        },
      },
    });
    const result = await executeEvalForRow({
      evalItem: { template_id: "tpl-c", template_type: "composite" },
      rowType: "Span",
      currentRow: { span_id: "s1" },
      spanDetail: {},
      mapping: {},
    });
    expect(result.output).toBeNull();
  });
});
