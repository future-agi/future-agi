import { describe, expect, it } from "vitest";
import {
  defaultSamplingPolicy,
  samplingCoordinationErrors,
  samplingPolicyForApi,
  samplingRuleErrors,
  withCoordination,
} from "./samplingPolicyForm";
import {
  isSimpleSamplingPolicy,
  samplingContextError,
  samplingMatcherValue,
  simpleGroupsForValues,
  simpleSamplingPolicy,
} from "./simpleSamplingPolicy";
const values = {
  rowType: "spans",
  runType: "historical",
  startDate: "2026-10-01T00:00:00.000Z",
  endDate: "2026-10-02T00:00:00.000Z",
  samplingRate: 50,
};

describe("opaque advanced settings and unchanged rule validation", () => {
  it.each(["min_n", "max_n", "rare_below"])(
    "retains zero %s and rejects blanks, fractions, negatives and excessive values",
    (key) => {
      expect(samplingRuleErrors({ target_n: 10, [key]: 0 })).toEqual({});
      for (const bad of ["", -1, 0.5, 1000001, Infinity, NaN])
        expect(samplingRuleErrors({ target_n: 10, [key]: bad })).toHaveProperty(
          key,
        );
      const policy = {
        ...defaultSamplingPolicy(),
        default: { target_n: 10, [key]: 0 },
      };
      expect(isSimpleSamplingPolicy(policy)).toBe(false);
      expect(samplingPolicyForApi(policy)).toEqual(policy);
    },
  );
  it("does not permit a minimum greater than its maximum", () => {
    expect(
      samplingRuleErrors({ target_n: 10, min_n: 2, max_n: 1 }),
    ).toHaveProperty("min_n");
  });
  it("keeps independent policies key-free and validates explicit project coordination without mutating the input", () => {
    const original = simpleSamplingPolicy(values);
    const coordinated = {
      ...withCoordination(original, "project"),
      panel: "q4",
      prn_start: 12.5,
    };
    expect(samplingCoordinationErrors(coordinated)).toEqual({});
    expect(isSimpleSamplingPolicy(coordinated)).toBe(false);
    expect(withCoordination(coordinated, "independent")).toEqual(original);
    expect(original).not.toHaveProperty("coordination");
    expect(samplingPolicyForApi(coordinated)).toEqual(coordinated);
    for (const bad of [-1, 100, 1.0000001, "", NaN])
      expect(
        samplingCoordinationErrors({ ...coordinated, prn_start: bad }),
      ).toHaveProperty("prn_start");
    for (const bad of ["!", " ", "a".repeat(65)])
      expect(
        samplingCoordinationErrors({ ...coordinated, panel: bad }),
      ).toHaveProperty("panel");
  });
  it("recognizes a normalized server policy without dropping its default filters or independent setting", () => {
    const normalized = {
      ...simpleSamplingPolicy(values),
      default: { target_n: 100, filters: [] },
      coordination: "independent",
    };
    expect(isSimpleSamplingPolicy(normalized)).toBe(true);
    expect(samplingPolicyForApi(normalized)).toEqual(normalized);
    expect(
      isSimpleSamplingPolicy({
        ...normalized,
        default: { ...normalized.default, filters: {} },
      }),
    ).toBe(false);
    expect(isSimpleSamplingPolicy({ ...normalized, group_by: {} })).toBe(false);
  });
  it("keeps typed values and disjoint first-match assignment, existing order, rule names and zero quotas", () => {
    const field = { source: "metadata", key: "contact.language" };
    const policy = simpleSamplingPolicy(values);
    policy.strata = simpleGroupsForValues(policy, field, [
      "1",
      2,
      false,
      "French, English",
    ]);
    policy.strata[0].target_n = 0;
    const before = structuredClone(policy);
    expect(isSimpleSamplingPolicy(policy)).toBe(true);
    expect(
      simpleGroupsForValues(policy, field, ["1", 2, false, "French, English"]),
    ).toEqual(before.strata);
    expect(policy).toEqual(before);
    const repeated = {
      ...policy,
      strata: [...policy.strata, { ...policy.strata[0], name: "duplicate" }],
    };
    expect(isSimpleSamplingPolicy(repeated)).toBe(false);
  });
  it.each([
    [42, "42"],
    [false, "false"],
    [0.00001, "1e-05"],
  ])(
    "keeps matcher-alias groups %j read-only instead of hiding first-match precedence",
    (numeric, text) => {
      expect(samplingMatcherValue(numeric)).toBe(samplingMatcherValue(text));
      const policy = simpleSamplingPolicy(values);
      policy.strata = simpleGroupsForValues(
        policy,
        { source: "span", key: "locale" },
        [numeric, text],
      );
      const before = structuredClone(policy);
      expect(isSimpleSamplingPolicy(policy)).toBe(false);
      expect(samplingPolicyForApi(policy)).toEqual(before);
      expect(policy).toEqual(before);
    },
  );
  it("blocks incompatible rows, future/invalid windows and live historical schedules without rewriting a policy", () => {
    const policy = simpleSamplingPolicy(values),
      before = structuredClone(policy);
    expect(
      samplingContextError({
        ...values,
        rowType: "sessions",
        samplingPolicy: { ...policy, sampling_unit: "span" },
      }),
    ).toMatch(/cannot be finer/);
    const live = {
      ...policy,
      mode: "live",
      default: { rate: 10 },
      window: { start: values.startDate, end: null },
    };
    expect(samplingContextError({ ...values, samplingPolicy: live })).toMatch(
      /New incoming data schedule/,
    );
    expect(
      samplingContextError({
        ...values,
        samplingPolicy: {
          ...policy,
          window: { start: "", end: values.endDate },
        },
      }),
    ).toMatch(/valid past sampling window/);
    expect(
      samplingContextError({
        ...values,
        samplingPolicy: { ...live, window: { start: null, end: null } },
        runType: "continuous",
      }),
    ).toMatch(/valid past sampling window/);
    expect(policy).toEqual(before);
  });
});
