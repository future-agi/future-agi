import { describe, expect, it } from "vitest";

import { buildCompositeRuntimeConfig } from "../../Helpers/compositeRuntimeConfig";

describe("buildCompositeRuntimeConfig", () => {
  it("returns an empty object when no config or params are provided", () => {
    expect(buildCompositeRuntimeConfig()).toEqual({});
  });

  it("adds function params to the runtime config", () => {
    expect(
      buildCompositeRuntimeConfig({
        codeParams: { min_words: 100, max_words: 200 },
      }),
    ).toEqual({
      params: { min_words: 100, max_words: 200 },
    });
  });

  it("preserves unrelated config fields while merging params", () => {
    expect(
      buildCompositeRuntimeConfig({
        config: { provider: "openai", threshold: 0.5 },
        codeParams: { min_words: 100 },
      }),
    ).toEqual({
      provider: "openai",
      threshold: 0.5,
      params: { min_words: 100 },
    });
  });

  it("merges existing params with function params and prefers explicit function params", () => {
    expect(
      buildCompositeRuntimeConfig({
        config: { params: { model_name: "gpt-4", min_words: 10 } },
        codeParams: { min_words: 100, max_words: 200 },
      }),
    ).toEqual({
      params: {
        model_name: "gpt-4",
        min_words: 100,
        max_words: 200,
      },
    });
  });
});
