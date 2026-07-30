import { describe, it, expect } from "vitest";
import { buildRunConfig } from "./buildRunConfig";

describe("buildRunConfig", () => {
  it("forwards single-eval runtime overrides", () => {
    const runConfig = buildRunConfig(
      {
        model: "turing_large",
        agent_mode: "agent",
        check_internet: true,
        summary: { type: "concise" },
        knowledge_bases: ["kb-1"],
        tools: { web: true },
        pass_threshold: 0.7,
        choice_scores: { yes: 1 },
        multi_choice: false,
        data_injection: { variables_only: true },
        error_localizer_enabled: true,
      },
      { isComposite: false },
    );

    expect(runConfig).toEqual({
      model: "turing_large",
      agent_mode: "agent",
      check_internet: true,
      summary: { type: "concise" },
      knowledge_bases: ["kb-1"],
      tools: { web: true },
      pass_threshold: 0.7,
      choice_scores: { yes: 1 },
      multi_choice: false,
      data_injection: { variables_only: true },
      error_localizer_enabled: true,
    });
  });

  it("keeps composite bindings free of single-eval overrides", () => {
    const runConfig = buildRunConfig(
      {
        model: "turing_large",
        agent_mode: "agent",
        model_params: { temperature: 0 },
        data_injection: { variables_only: true },
      },
      { isComposite: true },
    );

    expect(runConfig).toEqual({
      data_injection: { variables_only: true },
    });
  });

  it("forwards model_params when present, preserving zero values", () => {
    const runConfig = buildRunConfig(
      { model_params: { temperature: 0, max_tokens: 256 } },
      { isComposite: false },
    );

    expect(runConfig.model_params).toEqual({ temperature: 0, max_tokens: 256 });
  });

  it("omits model_params when absent or empty", () => {
    expect(buildRunConfig({}, { isComposite: false })).not.toHaveProperty(
      "model_params",
    );
    expect(
      buildRunConfig({ model_params: {} }, { isComposite: false }),
    ).not.toHaveProperty("model_params");
    expect(
      buildRunConfig({ model_params: null }, { isComposite: false }),
    ).not.toHaveProperty("model_params");
  });

  it("keeps falsy-but-meaningful values (pass_threshold 0, multi_choice false)", () => {
    const runConfig = buildRunConfig(
      { pass_threshold: 0, multi_choice: false, check_internet: false },
      { isComposite: false },
    );

    expect(runConfig.pass_threshold).toBe(0);
    expect(runConfig.multi_choice).toBe(false);
    expect(runConfig.check_internet).toBe(false);
  });
});
