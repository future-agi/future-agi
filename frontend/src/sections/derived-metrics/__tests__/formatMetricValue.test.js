import { describe, expect, it } from "vitest";
import { formatMetricInterval, formatMetricValue } from "../formatMetricValue";
import { intervalUnavailableCopy, metricCountCopy } from "../copy";

const number = { value_type: "number", value_format: "number", unit: "" };
const rate = { ...number, value_format: "percent", rate_eligible: true };
const uncertainty = (overrides = {}) => ({
  confidence_level: 0.95,
  status: "ok",
  reason: "",
  method: "poisson",
  interval: "wilson",
  std_error: 0.04,
  ci_low: 0.53,
  ci_high: 0.7,
  effective_n: 75,
  degrees_of_freedom: 74,
  ...overrides,
});

describe("scalar formatting", () => {
  it.each([null, undefined, NaN, Infinity, -Infinity, "0", false, {}, []])(
    "does not turn %s into a numeric value",
    (value) => {
      expect(formatMetricValue(value, number, "en-US")).toBeNull();
    },
  );
  it.each([
    [0, "0"],
    [-0, "0"],
    [123456789, "123,456,789"],
    [-2.5, "-2.5"],
    [1e-10, "0.0000000001"],
  ])("preserves numeric precision for %s", (value, text) => {
    expect(formatMetricValue(value, number, "en-US")).toBe(text);
  });
  it("does not infer a percentage from value range, kind, or weighting", () => {
    expect(
      formatMetricValue(
        0.25,
        { ...number, kind: "share", weighted: true },
        "en-US",
      ),
    ).toBe("0.25");
  });
  it.each([
    [0, "0%"],
    [0.25, "25%"],
    [1, "100%"],
    [1e-10, "0.00000001%"],
  ])("formats a proven rate %s", (value, text) => {
    expect(formatMetricValue(value, rate, "en-US")).toBe(text);
  });
  it("requires the explicit point rate proof even with percent metadata", () => {
    expect(
      formatMetricValue(
        0.25,
        { ...rate, rate_eligible: false, unit: "%" },
        "en-US",
      ),
    ).toBe("0.25");
  });
  it("accepts the catalog's numeric percent projection without inventing a rate_eligible field", () => {
    expect(
      formatMetricValue(
        0.25,
        {
          ...number,
          projection: true,
          value_format: "percent",
          value_labels: { match: "Resolved" },
        },
        "en-US",
      ),
    ).toBe("25%");
  });
  it("renders the count projection as its numeric total", () => {
    expect(
      formatMetricValue(
        0,
        { ...number, projection: true, unit: "total" },
        "en-US",
      ),
    ).toBe("0 total");
    expect(
      formatMetricValue(
        4,
        { ...number, projection: true, unit: "total" },
        "en-US",
      ),
    ).toBe("4 total");
  });
  it.each(["ms", "tokens", "USD", "requests / conversation"])(
    "keeps declared units %s on sampled totals/ratios",
    (unit) => {
      expect(
        formatMetricValue(1250.5, { ...number, unit, weighted: true }, "en-US"),
      ).toBe(`1,250.5 ${unit}`);
    },
  );
  it("uses locale-aware numbers and percents", () => {
    expect(formatMetricValue(1234.5, number, "fr-FR")).toBe("1 234,5");
    expect(formatMetricValue(0.125, rate, "fr-FR")).toBe("12,5 %");
  });
  it.each([
    [true, "Counts"],
    [false, "Doesn't count"],
  ])("renders Boolean %s as %s", (value, text) => {
    expect(
      formatMetricValue(value, { value_type: "boolean", value_labels: {} }),
    ).toBe(text);
  });
  it("uses match/other labels only for Boolean values", () => {
    const value_labels = { match: "Resolved", other: "Not resolved" };
    expect(
      formatMetricValue(false, { value_type: "boolean", value_labels }),
    ).toBe("Not resolved");
    expect(formatMetricValue(0, { ...number, value_labels }, "en-US")).toBe(
      "0",
    );
    expect(
      formatMetricValue(false, { value_type: "boolean", projection: true }),
    ).toBeNull();
    expect(formatMetricValue(0, { value_type: "boolean" })).toBeNull();
  });
  it("keeps empty text distinct from null text", () => {
    expect(formatMetricValue("", { value_type: "text" })).toBe("Empty text");
    expect(formatMetricValue(null, { value_type: "text" })).toBeNull();
    expect(formatMetricValue("0", { value_type: "text" })).toBe("0");
    expect(formatMetricValue(0, { value_type: "text" })).toBeNull();
    expect(formatMetricValue(0, { value_type: "future_type" })).toBeNull();
  });
});

describe("returned uncertainty", () => {
  it("preserves asymmetric rate bounds without deriving a margin", () => {
    expect(
      formatMetricInterval(
        { ...rate, value: 0.62, uncertainty: uncertainty() },
        "en-US",
      ),
    ).toBe("95% interval: 53% – 70%");
  });
  it("keeps total bounds in their original units, including negative bounds", () => {
    expect(
      formatMetricInterval(
        {
          ...number,
          unit: "tokens",
          weighted: true,
          uncertainty: uncertainty({
            interval: "wald_t",
            ci_low: -20,
            ci_high: 130,
          }),
        },
        "en-US",
      ),
    ).toBe("95% interval: -20 tokens – 130 tokens");
  });
  it("does not clamp or turn an arbitrary ratio's bounds into a rate", () => {
    expect(
      formatMetricInterval(
        {
          ...number,
          weighted: true,
          rate_eligible: false,
          uncertainty: uncertainty({ ci_low: 0, ci_high: 2.1 }),
        },
        "en-US",
      ),
    ).toBe("95% interval: 0 – 2.1");
  });
  it("accepts exact zero census bounds", () => {
    expect(
      formatMetricInterval(
        { ...rate, uncertainty: uncertainty({ ci_low: 0, ci_high: 0 }) },
        "en-US",
      ),
    ).toBe("95% interval: 0% – 0%");
  });
  it("does not invent a confidence level", () => {
    expect(
      formatMetricInterval(
        { ...number, uncertainty: uncertainty({ confidence_level: null }) },
        "en-US",
      ),
    ).toBe("Interval: 0.53 – 0.7");
  });
  it.each([
    [null, 0.7],
    [0.53, null],
    [NaN, 1],
    [0, Infinity],
    [1, 0],
    ["0", 1],
  ])("rejects unavailable or invalid bounds %s..%s", (ci_low, ci_high) => {
    expect(
      formatMetricInterval(
        { ...rate, uncertainty: uncertainty({ ci_low, ci_high }) },
        "en-US",
      ),
    ).toBe("Interval unavailable: reason unavailable");
  });
  it.each([
    "no_value",
    "design_not_captured",
    "mixed_design",
    "unweighted_total",
    "fewer_than_two_units",
    "population_size_missing",
    "inconsistent_stratum_design",
    "single_unit_stratum",
    "no_degrees_of_freedom",
    "non_finite",
    "unstable_variance",
    "not_computed",
  ])(
    "renders returned unavailable reason %s even if obsolete bounds are present",
    (reason) => {
      const text = formatMetricInterval({
        ...rate,
        uncertainty: uncertainty({ status: "not_estimable", reason }),
      });
      expect(text).toBe(intervalUnavailableCopy(reason));
      expect(text).not.toContain("reason unavailable");
      expect(text).not.toContain("53%");
    },
  );
  it("does not fabricate an interval for heterogeneous weights", () => {
    expect(
      formatMetricInterval({
        ...rate,
        weighted: true,
        uncertainty: uncertainty({
          status: "not_estimable",
          reason: "unstable_variance",
          effective_n: 87,
          ci_low: null,
          ci_high: null,
        }),
      }),
    ).toBe(
      "Interval unavailable: insufficient support for a reliable interval",
    );
  });
  it("fails closed for absent uncertainty and unknown reasons", () => {
    expect(formatMetricInterval({})).toBe(
      "Interval unavailable: reason unavailable",
    );
    expect(intervalUnavailableCopy("__proto__")).toBe(
      "Interval unavailable: reason unavailable",
    );
  });
});

describe("server-declared diagnostic counts", () => {
  it("uses returned noun and counts without reverse-engineering a percent", () => {
    expect(
      metricCountCopy(
        { eligible_count: 1200, evaluated_count: 120 },
        "traces",
        "en-US",
      ),
    ).toEqual(["from 120 evaluated of 1,200 traces"]);
  });
  it("preserves zero users and no-value users as separate returned counts", () => {
    expect(
      metricCountCopy(
        {
          counted_users: 0,
          eligible_users: 4,
          no_value_users: 2,
          conversations_without_user: 3,
        },
        "users",
        "en-US",
      ),
    ).toEqual([
      "0 of 4 users",
      "2 users with no value",
      "3 conversations without a single user",
    ]);
  });
  it("describes missing or ambiguous user attribution without claiming no user exists", () => {
    expect(
      metricCountCopy({ conversations_without_user: 3 }, "users", "en-US"),
    ).toEqual(["3 conversations without a single user"]);
  });
  it.each([null, undefined, -1, NaN, "0", 1.5])(
    "does not invent a missing or malformed count %s",
    (count) => {
      expect(
        metricCountCopy(
          { eligible_count: count, evaluated_count: count },
          "conversations",
        ),
      ).toEqual([]);
    },
  );
});

// Round 3 UI: headline positions round for reading; tables keep six digits.
describe("display precision", () => {
  const display = (value, metadata) =>
    formatMetricValue(value, metadata, "en-US", { display: true });
  it.each([
    [0.801887, rate, "80.2%"],
    [0.8, rate, "80%"],
    [3.99685, number, "3.997"],
    [12.3456, number, "12.35"],
    [2445.32, { ...number, unit: "ms" }, "2,445.32 ms"],
    [1234567.891, number, "1,234,567.89"],
    [0.000123456, number, "0.0001235"],
  ])("rounds %s for a headline position", (value, metadata, text) => {
    expect(display(value, metadata)).toBe(text);
  });
  it.each([
    [0.0004, "0.04%"],
    [-0.0004, "-0.04%"],
    [0.99996, "99.996%"],
  ])(
    "never rounds %s onto a 0%% or 100%% boundary it does not reach",
    (value, text) => {
      expect(display(value, rate)).toBe(text);
    },
  );
  it("leaves the default formatting and missing values unchanged", () => {
    expect(formatMetricValue(0.801887, rate, "en-US")).toBe("80.1887%");
    expect(display(null, rate)).toBeNull();
    expect(display(0, rate)).toBe("0%");
  });
});
