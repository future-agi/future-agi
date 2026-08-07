import { describe, expect, it } from "vitest";
import {
  buildEvalRunConfig,
  resolveCompositeWeightOverrides,
} from "./buildEvalRunConfig";

describe("buildEvalRunConfig", () => {
  it("nests single-eval runtime fields and shared toggles", () => {
    expect(
      buildEvalRunConfig({
        model: "gpt-4o",
        agent_mode: "react",
        check_internet: true,
        error_localizer_enabled: true,
        data_injection: { full_row: true },
        pass_threshold: 0.7,
      }),
    ).toEqual({
      model: "gpt-4o",
      agent_mode: "react",
      check_internet: true,
      pass_threshold: 0.7,
      data_injection: { full_row: true },
      error_localizer_enabled: true,
    });
  });

  it("omits single-eval fields for composites but keeps shared toggles", () => {
    expect(
      buildEvalRunConfig(
        {
          model: "gpt-4o",
          error_localizer_enabled: false,
          data_injection: { variables_only: true },
        },
        { isComposite: true },
      ),
    ).toEqual({
      data_injection: { variables_only: true },
      error_localizer_enabled: false,
    });
  });
});

describe("resolveCompositeWeightOverrides", () => {
  it("prefers camel then falls back to snake", () => {
    expect(
      resolveCompositeWeightOverrides({
        compositeWeightOverrides: { a: 1 },
        composite_weight_overrides: { a: 2 },
      }),
    ).toEqual({ a: 1 });
    expect(
      resolveCompositeWeightOverrides({
        composite_weight_overrides: { a: 2 },
      }),
    ).toEqual({ a: 2 });
    expect(resolveCompositeWeightOverrides({})).toBeNull();
  });
});
