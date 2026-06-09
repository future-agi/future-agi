import { describe, expect, it } from "vitest";

import {
  buildVoiceCallAnnotationSources,
  buildVoiceCallScoreSource,
} from "../voiceAnnotationSources";

describe("voice call annotation source selection", () => {
  it("uses trace as the direct annotation source for observed calls", () => {
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
