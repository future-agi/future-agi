import { describe, expect, it } from "vitest";

import {
  buildTraceAnnotationSources,
  buildVoiceCallAnnotationSources,
  buildVoiceCallScoreSource,
} from "../voiceAnnotationSources";

describe("voice call annotation source selection", () => {
  it("returns every level of source so trace, span and session queues are all visible", () => {
    expect(
      buildVoiceCallAnnotationSources({
        traceId: "trace-1",
        rootSpanId: "span-1",
        module: "project",
      }),
    ).toEqual([
      {
        sourceType: "trace",
        sourceId: "trace-1",
        spanNotesSourceId: "span-1",
      },
      { sourceType: "observation_span", sourceId: "span-1" },
    ]);
  });

  it("includes trace_session when the voice call belongs to a session", () => {
    expect(
      buildVoiceCallAnnotationSources({
        traceId: "trace-1",
        rootSpanId: "span-1",
        sessionId: "session-1",
        module: "project",
      }),
    ).toEqual([
      {
        sourceType: "trace",
        sourceId: "trace-1",
        spanNotesSourceId: "span-1",
      },
      { sourceType: "observation_span", sourceId: "span-1" },
      {
        sourceType: "trace_session",
        sourceId: "session-1",
        spanNotesSourceId: "span-1",
      },
    ]);
  });

  it("uses trace as the primary score source and keeps span as secondary display", () => {
    expect(
      buildVoiceCallScoreSource({
        traceId: "trace-1",
        rootSpanId: "span-1",
        isSimulate: false,
      }),
    ).toEqual({
      sourceType: "trace",
      sourceId: "trace-1",
      secondarySourceType: "observation_span",
      secondarySourceId: "span-1",
    });
  });

  it("falls back to call_execution for simulate calls without trace observability", () => {
    expect(
      buildVoiceCallAnnotationSources({
        module: "simulate",
        callExecutionId: "call-1",
      }),
    ).toEqual([{ sourceType: "call_execution", sourceId: "call-1" }]);
  });
});

describe("buildTraceAnnotationSources", () => {
  it("returns trace + span sources so trace-level queues are no longer hidden", () => {
    expect(
      buildTraceAnnotationSources({
        traceId: "trace-1",
        spanId: "span-1",
      }),
    ).toEqual([
      {
        sourceType: "trace",
        sourceId: "trace-1",
        spanNotesSourceId: "span-1",
      },
      { sourceType: "observation_span", sourceId: "span-1" },
    ]);
  });

  it("includes trace_session when the trace belongs to a session", () => {
    expect(
      buildTraceAnnotationSources({
        traceId: "trace-1",
        spanId: "span-1",
        sessionId: "session-1",
      }),
    ).toEqual([
      {
        sourceType: "trace",
        sourceId: "trace-1",
        spanNotesSourceId: "span-1",
      },
      { sourceType: "observation_span", sourceId: "span-1" },
      {
        sourceType: "trace_session",
        sourceId: "session-1",
        spanNotesSourceId: "span-1",
      },
    ]);
  });

  it("returns only what is available so partial trace data is handled gracefully", () => {
    expect(buildTraceAnnotationSources({ traceId: "trace-1" })).toEqual([
      { sourceType: "trace", sourceId: "trace-1" },
    ]);
    expect(buildTraceAnnotationSources({ spanId: "span-1" })).toEqual([
      { sourceType: "observation_span", sourceId: "span-1" },
    ]);
    expect(buildTraceAnnotationSources({})).toEqual([]);
  });
});
