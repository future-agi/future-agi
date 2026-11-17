import { describe, expect, it } from "vitest";

import { getCustomModelFields, getModelFields } from "../helper";
import { customModelValidation } from "../validation";

const customModel = (overrides) => ({
  modelProvider: "custom",
  modelName: "llama-3-local",
  inputTokenCost: 0.5,
  outputTokenCost: 1.5,
  ...overrides,
});

const costErrors = (overrides) => {
  const result = customModelValidation("FORM").safeParse(
    customModel(overrides),
  );
  if (result.success) return {};
  return Object.fromEntries(
    result.error.issues
      .filter((issue) => /TokenCost$/.test(issue.path[0]))
      .map((issue) => [issue.path[0], issue.message]),
  );
};

describe("custom model token cost validation", () => {
  it("accepts 0 for self-hosted models that cost nothing", () => {
    expect(costErrors({ inputTokenCost: 0, outputTokenCost: 0 })).toEqual({});
  });

  it("still requires a value", () => {
    expect(costErrors({ inputTokenCost: "", outputTokenCost: NaN })).toEqual({
      inputTokenCost: "Input token cost is required",
      outputTokenCost: "Output token cost is required",
    });
  });

  it("rejects negative costs", () => {
    expect(costErrors({ inputTokenCost: -1 })).toEqual({
      inputTokenCost: "Input token cost cannot be negative",
    });
  });

  it("does not set a browser minimum above 0 on the cost inputs", () => {
    const costFields = [...getModelFields(), ...getCustomModelFields()].filter(
      (field) => /TokenCost$/.test(field.fieldName),
    );

    expect(costFields).toHaveLength(4);
    costFields.forEach((field) => expect(field.inputProps.min).toBe(0));
  });
});

describe("custom model API Base URL field", () => {
  it("asks for the full chat-completions URL, not a /v1 base", () => {
    const apiBase = getCustomModelFields().find(
      (field) => field.fieldName === "apiBase",
    );

    expect(apiBase.placeholder).toMatch(/\/v1\/chat\/completions$/);
    expect(apiBase.helperText).toMatch(/full chat-completions URL/);
  });
});
