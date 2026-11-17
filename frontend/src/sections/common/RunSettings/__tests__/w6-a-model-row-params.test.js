// W6 A2: an added model row inherits the first row's params.
import { describe, expect, it } from "vitest";
import { inheritedParams } from "../modelRowParams";
import { preloadParams } from "src/sections/develop-detail/Experiment/StepperComponentExperiment/NewModelRenderWithParamsTool";

describe("inheritedParams", () => {
  it("prefers the first row, then the version", () => {
    const first = { temperature: 0.7, maxTokens: 1024, frequencyPenalty: 0 };
    const version = { temperature: 0.3, max_tokens: 400, top_p: 0.9 };
    expect(inheritedParams(first, version)).toEqual({
      temperature: 0.7,
      maxTokens: 1024,
      topP: 0.9,
    });
    expect(inheritedParams(null, version)).toEqual({
      temperature: 0.3,
      maxTokens: 400,
      topP: 0.9,
    });
    expect(inheritedParams(null, null)).toBeNull();
  });
  it("feeds the row's preload (the popover starts at the first row's values)", () => {
    const defaults = { temperature: 1, maxTokens: 256, topP: 1 };
    expect(
      preloadParams(
        defaults,
        inheritedParams(
          { temperature: 0.7, maxTokens: 1024 },
          { temperature: 0.3, max_tokens: 400 },
        ),
      ),
    ).toEqual({ temperature: 0.7, maxTokens: 1024, topP: 1 });
  });
});
