import { describe, expect, it } from "vitest";

import {
  buildCompositeChildConfigs,
  buildCompositeRuntimeConfig,
} from "../../Helpers/compositeRuntimeConfig";

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

describe("buildCompositeChildConfigs", () => {
  it("maps child code params to per-child runtime config", () => {
    expect(
      buildCompositeChildConfigs([
        {
          child_id: "word-count",
          config: { params: { min_words: 5, max_words: 20 } },
        },
        { child_id: "refusal", config: {} },
      ]),
    ).toEqual({
      "word-count": {
        params: { min_words: 5, max_words: 20 },
      },
    });
  });

  it("prefers top-level params when a picker payload carries them", () => {
    expect(
      buildCompositeChildConfigs([
        {
          child_id: "word-count",
          params: { min_words: 3 },
          config: { params: { min_words: 1 } },
        },
      ]),
    ).toEqual({
      "word-count": {
        params: { min_words: 3 },
      },
    });
  });
});
