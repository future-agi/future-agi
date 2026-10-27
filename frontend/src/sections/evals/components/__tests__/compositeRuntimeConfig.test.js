import { describe, expect, it } from "vitest";

import {
  buildCompositeChildConfigs,
  buildCompositeChildRunConfig,
  buildCompositeRuntimeConfig,
} from "../../Helpers/compositeRuntimeConfig";
import { normalizeEvalPickerEval } from "src/sections/common/EvalPicker/evalPickerValue";

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

describe("buildCompositeChildRunConfig", () => {
  it("returns an empty object when nothing is provided", () => {
    expect(buildCompositeChildRunConfig()).toEqual({});
    expect(buildCompositeChildRunConfig({})).toEqual({});
  });

  it("reads the snake_case config-screen payload", () => {
    expect(
      buildCompositeChildRunConfig({
        model: "gpt-4o",
        pass_threshold: 0.8,
        check_internet: true,
        agent_mode: "single",
        knowledge_bases: ["kb-1"],
        data_injection: { dataset: true },
        output_type: "pass_fail",
      }),
    ).toEqual({
      model: "gpt-4o",
      pass_threshold: 0.8,
      check_internet: true,
      agent_mode: "single",
      knowledge_bases: ["kb-1"],
      data_injection: { dataset: true },
    });
  });

  it("reads the camelized skipConfig payload the same way", () => {
    const snakeCase = {
      model: "gpt-4o",
      pass_threshold: 0.8,
      check_internet: true,
      agent_mode: "single",
      knowledge_bases: ["kb-1"],
      data_injection: { dataset: true },
    };

    expect(
      buildCompositeChildRunConfig(normalizeEvalPickerEval(snakeCase)),
    ).toEqual(buildCompositeChildRunConfig(snakeCase));
  });

  it("falls back to config.run_config in either case", () => {
    expect(
      buildCompositeChildRunConfig({
        config: { run_config: { model: "gpt-4o", pass_threshold: 0.7 } },
      }),
    ).toEqual({ model: "gpt-4o", pass_threshold: 0.7 });

    expect(
      buildCompositeChildRunConfig({
        config: { runConfig: { model: "gpt-4o", passThreshold: 0.7 } },
      }),
    ).toEqual({ model: "gpt-4o", pass_threshold: 0.7 });
  });

  it("prefers the top-level value over the nested one", () => {
    expect(
      buildCompositeChildRunConfig({
        model: "gpt-4o",
        config: { run_config: { model: "turing_large" } },
      }),
    ).toEqual({ model: "gpt-4o" });
  });

  it("keeps explicit false and zero so they can override template defaults", () => {
    expect(
      buildCompositeChildRunConfig({
        check_internet: false,
        pass_threshold: 0,
      }),
    ).toEqual({ check_internet: false, pass_threshold: 0 });
  });

  it("drops empty collections rather than clearing template values", () => {
    expect(
      buildCompositeChildRunConfig({
        tools: [],
        knowledge_bases: [],
        data_injection: {},
        model: "gpt-4o",
      }),
    ).toEqual({ model: "gpt-4o" });
  });

  it("only persists an explicit error localizer opt-in", () => {
    expect(
      buildCompositeChildRunConfig({ error_localizer_enabled: false }),
    ).toEqual({});
    expect(
      buildCompositeChildRunConfig({ error_localizer_enabled: true }),
    ).toEqual({ error_localizer_enabled: true });
    expect(
      buildCompositeChildRunConfig(
        normalizeEvalPickerEval({ error_localizer_enabled: true }),
      ),
    ).toEqual({ error_localizer_enabled: true });
  });
});
