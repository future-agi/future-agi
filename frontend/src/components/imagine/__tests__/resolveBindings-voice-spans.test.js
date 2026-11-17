import { describe, expect, it } from "vitest";
import {
  buildContext,
  flattenSpans,
  resolveBindings,
} from "../resolveBindings";

// Voice and chat call detail sends observation_span as a flat list of span
// objects (root conversation span first), not the trace tree's
// { observation_span, children } entries.
const voiceSpans = [
  {
    id: "root",
    name: "conversation",
    observation_type: "conversation",
    latency_ms: 95000,
    parent_span_id: null,
  },
  {
    id: "llm-1",
    name: "llm turn 1",
    observation_type: "llm",
    latency_ms: 420,
    parent_span_id: "root",
  },
  {
    id: "llm-2",
    name: "llm turn 2",
    observation_type: "llm",
    latency_ms: 640,
    parent_span_id: "root",
  },
];

const voiceCall = (spans = voiceSpans, summary = {}) => ({
  spans,
  summary,
  transcript: [],
  trace: { id: "call-1" },
});

const metric = (dataBinding) => ({
  id: "metric",
  type: "metric_card",
  dataBinding,
});

const resolveOne = (widget, traceData) =>
  resolveBindings([widget], traceData)[0].config;

describe("Imagine bindings on a voice call (flat spans)", () => {
  it("keeps flat voice spans instead of dropping them", () => {
    expect(flattenSpans(voiceSpans).map((span) => span.id)).toEqual([
      "root",
      "llm-1",
      "llm-2",
    ]);
    expect(buildContext(voiceCall()).rootSpan.name).toBe("conversation");
  });

  it("fills span tables from a voice call", () => {
    const config = resolveOne(
      {
        id: "table",
        type: "data_table",
        dataBinding: {
          rowsFromSpans: true,
          columns: ["name", { field: "latency_ms", headerName: "Latency" }],
        },
      },
      voiceCall(),
    );
    expect(config.rows).toEqual([
      { name: "conversation", latency_ms: 95000 },
      { name: "llm turn 1", latency_ms: 420 },
      { name: "llm turn 2", latency_ms: 640 },
    ]);
  });

  it("computes span metrics from a voice call", () => {
    expect(
      resolveOne(
        metric({ compute: "count(spans.name)", valueFormat: "{value} turns" }),
        voiceCall(),
      ).value,
    ).toBe("3 turns");
    expect(
      resolveOne(
        metric({
          compute: "max(spans.latency_ms, observation_type=llm)",
          valueFormat: "{value}ms",
        }),
        voiceCall(),
      ).value,
    ).toBe("640ms");
  });
});

describe("Imagine metric cards with no matching spans", () => {
  it("shows a dash, not 0, when the call has no spans", () => {
    const latency = metric({
      compute: "max(spans.latency_ms, observation_type=llm)",
      valueFormat: "{value}ms",
    });
    const turns = metric({
      compute: "count(spans.name)",
      valueFormat: "{value} turns",
    });
    expect(resolveOne(latency, voiceCall([])).value).toBe("—");
    expect(resolveOne(turns, voiceCall([])).value).toBe("—");
    expect(resolveOne(latency, undefined).value).toBe("—");
  });

  it("shows a dash when the filter matches no spans", () => {
    expect(
      resolveOne(
        metric({
          compute: "max(spans.latency_ms, observation_type=tool)",
          valueFormat: "{value}ms",
        }),
        voiceCall(),
      ).value,
    ).toBe("—");
  });

  it("shows a dash for arithmetic and subtitles that use an empty aggregate", () => {
    const config = resolveOne(
      metric({
        compute:
          "summary.totalDurationMs - max(spans.latency_ms, observation_type=tool)",
        valueFormat: "{value}ms",
        subtitleCompute: "avg(spans.latency_ms, observation_type=tool)",
        subtitleFormat: "avg {value}ms",
      }),
      voiceCall(voiceSpans, { totalDurationMs: 1000 }),
    );
    expect(config.value).toBe("—");
    expect(config.subtitle).toBe("—");
    expect(`${config.value} ${config.subtitle}`).not.toMatch(/null|undefined/);
  });
});

describe("Imagine bindings on a trace (span tree) still work", () => {
  const traceTree = [
    {
      observation_span: {
        id: "agent",
        name: "agent",
        observation_type: "chain",
        latency_ms: 900,
      },
      children: [
        {
          observation_span: {
            id: "llm",
            name: "llm",
            observation_type: "llm",
            latency_ms: 300,
          },
          children: [],
        },
      ],
    },
  ];
  const traceData = { spans: traceTree, summary: { totalDurationMs: 1000 } };

  it("flattens tree entries and computes from them", () => {
    expect(flattenSpans(traceTree).map((span) => span.id)).toEqual([
      "agent",
      "llm",
    ]);
    expect(
      resolveOne(
        metric({
          compute: "max(spans.latency_ms, observation_type=llm)",
          valueFormat: "{value}ms",
        }),
        traceData,
      ).value,
    ).toBe("300ms");
    expect(
      resolveOne(
        metric({
          compute:
            "summary.totalDurationMs - max(spans.latency_ms, observation_type=llm)",
        }),
        traceData,
      ).value,
    ).toBe("700");
  });
});
