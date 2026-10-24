import vectors from "../../../../../futureagi/tracer/tests/fixtures/sampling_stats_vectors.json";
import { describe, expect, it } from "vitest";
import {
  allocate,
  designEffect,
  kishEffectiveN,
  marginOfError,
  requiredSampleSize,
  stratifiedMarginOfError,
  weightCv,
  Z_VALUES,
} from "./samplingStats";

const close = (actual, expected) => {
  if (expected === null || expected === 0) expect(actual).toBe(expected);
  else
    expect(Math.abs(actual - expected)).toBeLessThanOrEqual(
      Math.abs(expected) * vectors.tolerance,
    );
};
describe("sampling statistics shared Python/JavaScript vectors", () => {
  it("uses the same standard normal quantiles", () =>
    expect(Z_VALUES).toEqual(vectors.z_values));
  it.each(vectors.margin_of_error)("margin $derivation", (v) =>
    close(
      marginOfError(v.n, v.population, v.proportion, v.confidence),
      v.expected,
    ),
  );
  it.each(vectors.required_sample_size)("minimum n $derivation", (v) => {
    const n = requiredSampleSize(
      v.margin,
      v.population,
      v.proportion,
      v.confidence,
    );
    expect(n).toBe(v.expected);
    if (n > 0)
      expect(
        marginOfError(n, v.population, v.proportion, v.confidence),
      ).toBeLessThanOrEqual(v.margin);
    if (n > 1)
      expect(
        marginOfError(n - 1, v.population, v.proportion, v.confidence),
      ).toBeGreaterThan(v.margin);
  });
  it.each(vectors.stratified_margin_of_error)(
    "stratified margin $derivation",
    (v) => close(stratifiedMarginOfError(v.cells, v.confidence), v.expected),
  );
  it.each(vectors.allocate)("allocation $derivation", (v) => {
    const call = () =>
      allocate(v.populations, v.total, v.method, v.minimum, v.proportions);
    if (v.error) expect(call).toThrow(v.error);
    else expect(call()).toEqual(v.expected);
  });
  it.each(vectors.kish)("Kish $derivation", (v) => {
    close(kishEffectiveN(v.weighted_counts), v.effective_n);
    close(designEffect(v.weighted_counts), v.design_effect);
    close(weightCv(v.weighted_counts), v.weight_cv);
  });
  it("rejects empty, fractional, nonfinite or out-of-domain numeric inputs", () => {
    [NaN, Infinity, -1, 0.5, "", true].forEach((bad) => {
      expect(() => marginOfError(bad, 10)).toThrow();
      expect(() => allocate([10], bad)).toThrow();
    });
    expect(() => marginOfError(11, 10)).toThrow();
    expect(() => allocate([10], 2, "neyman", 0, [NaN])).toThrow();
    expect(() => requiredSampleSize(0, 10)).toThrow();
    expect(() => kishEffectiveN([[0, 5]])).toThrow();
  });
  it("handles tiny margins and exact integer inversion boundaries", () => {
    expect(requiredSampleSize(1e-200, 100)).toBe(100);
    for (const n of [2, 50, 499]) {
      const margin = marginOfError(n, 500);
      expect(requiredSampleSize(margin, 500)).toBe(n);
      expect(requiredSampleSize(margin * (1 - 1e-12), 500)).toBe(n + 1);
    }
  });
  it("handles empty populations and zero-variance Neyman allocations", () => {
    expect(allocate([], 5)).toEqual([]);
    expect(allocate([0, 10], 100, "neyman", 1, [0, 1])).toEqual([0, 1]);
    expect(marginOfError(0, 0)).toBeNull();
  });
});
