import { describe, expect, it } from "vitest";
import {
  evalQualitySummary,
  isValidEvalQualityPoint,
} from "../evalQualityMetrics";
import {
  exactSummaryCases,
  invalidCases,
  qualityPoint,
  zeroPoint,
} from "./fixtures/evalQualityCases";

describe("evalQualitySummary", () => {
  it("distinguishes all errors from quality zero and exposes work counts", () => {
    const text = evalQualitySummary(
      [
        qualityPoint({
          count: 5,
          error_count: 2,
          pending_count: 1,
          running_count: 1,
          skipped_count: 1,
          unscored_count: 5,
        }),
      ],
      true,
    );
    expect(text).toBe(
      "Unscored · 2 execution errors (100.0%) · 0 quality failures · 1 pending · 1 running · 1 skipped · 5 without a quality score",
    );
  });
  it("keeps a genuine zero scored and ignores non-quality resource series", () => {
    expect(evalQualitySummary([zeroPoint], true)).toBe(
      "1 scored · 0 execution errors (0.0%) · 1 quality failure · 0 pending · 0 running · 0 skipped · 0 without a quality score",
    );
    expect(evalQualitySummary([{ value: 0 }], false)).toBe("");
  });
  it.each(exactSummaryCases)(
    "NK20 exact summary: $id",
    ({ input, isEvaluation, expectedExact }) => {
      expect(evalQualitySummary(input, isEvaluation)).toBe(expectedExact);
    },
  );
  it.each(invalidCases)(
    "NK20 validator and summary reject $id",
    ({ point }) => {
      expect(isValidEvalQualityPoint(point)).toBe(false);
      expect(evalQualitySummary([point], true)).toBe("Unavailable");
    },
  );
  it("accepts all valid supplied populations without requiring a numeric value", () => {
    for (const { input, isEvaluation, expectedExact } of exactSummaryCases) {
      if (isEvaluation === true && expectedExact !== "Unavailable") {
        for (const point of input)
          expect(isValidEvalQualityPoint(point)).toBe(true);
      }
    }
  });
  it("checks aggregate safety after validating each individual bucket", () => {
    const points = [
      qualityPoint({
        count: Number.MAX_SAFE_INTEGER,
        unscored_count: Number.MAX_SAFE_INTEGER,
      }),
      qualityPoint({ count: 1, unscored_count: 1 }),
    ];
    expect(points.every(isValidEvalQualityPoint)).toBe(true);
    expect(evalQualitySummary(points, true)).toBe("Unavailable");
  });
  it("preserves legacy untyped resource input and explicit non-EVAL context", () => {
    expect(evalQualitySummary()).toBe("");
    expect(evalQualitySummary([{ value: 0 }])).toBe("");
    expect(evalQualitySummary([{ count: 3, value: 0 }])).toBe("");
    expect(evalQualitySummary([zeroPoint], false)).toBe("");
    expect(evalQualitySummary(null, false)).toBe("");
  });
});
