import { describe, expect, it } from "vitest";

// Shared inputs, not a copy of the Go or frontend validation algorithm.
import fixtures from "../../../../../agentcc-gateway/internal/guardrails/contentmod/testdata/numeric_readiness.json";
import { selectedConfigErrors } from "./guardrailChecks";

describe("selected moderation numeric readiness parity", () => {
  it.each(fixtures)("$name", ({ config, threshold, valid }) => {
    const original = structuredClone(config);
    const errors = selectedConfigErrors("content-moderation", config, threshold);
    expect(Object.keys(errors).length === 0).toBe(valid);
    expect(config).toEqual(original);
  });

  it.each([NaN, Infinity, -Infinity])(
    "rejects nonfinite weight/count %s",
    (value) => {
      for (const config of [
        { weights: { toxicity: value } },
        { category_threshold: value },
      ]) {
        expect(
          Object.keys(selectedConfigErrors("content-moderation", config, 0))
            .length,
        ).toBeGreaterThan(0);
      }
    },
  );
});
