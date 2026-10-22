export const defaultSamplingPolicy = () => ({
  version: 1,
  sampling_unit: "session",
  missing_id: "exclude",
  mode: "closed",
  window: {
    start: new Date(Date.now() - 86400000).toISOString(),
    end: new Date(Date.now() - 1000).toISOString(),
  },
  strata: [],
  group_by: [],
  default: { rate: 10 },
});

// The selection mode is the rule's explicit key, never inferred from whether
// that key currently holds a value: a cleared quota keeps `target_n: ""` so the
// control cannot flip back to a percentage while the user is retyping.
export const selectionMode = (rule) =>
  rule && Object.hasOwn(rule, "target_n") ? "target_n" : "rate";

export const SAMPLING_QUOTA_LIMIT = 1000000;
const OPTIONAL_COUNTS = ["min_n", "max_n", "rare_below"];

const isWholeCount = (value) =>
  typeof value === "number" &&
  Number.isInteger(value) &&
  value >= 0 &&
  value <= SAMPLING_QUOTA_LIMIT;

const isRate = (value) =>
  typeof value === "number" &&
  Number.isFinite(value) &&
  value >= 0 &&
  value <= 100 &&
  Math.round(value * 1e6) / 1e6 === value;

// Field-level problems for one slice/default rule, keyed by policy key. A
// missing or blank required value is an error, never an implied 100% or 0.
export const samplingRuleErrors = (rule) => {
  const errors = {};
  if (!rule || typeof rule !== "object") {
    errors.rate = "Enter a rate from 0 to 100.";
    return errors;
  }
  const mode = selectionMode(rule);
  const value = rule[mode];
  if (mode === "target_n") {
    if (value === "" || value === undefined || value === null) {
      errors.target_n = "Enter the number of target units.";
    } else if (!isWholeCount(value)) {
      errors.target_n = "Use a whole number from 0 to 1,000,000.";
    }
  } else if (value === "" || value === undefined || value === null) {
    errors.rate = "Enter a rate from 0 to 100.";
  } else if (!isRate(value)) {
    errors.rate = "Use a percentage from 0 to 100 with at most six decimals.";
  }
  OPTIONAL_COUNTS.forEach((key) => {
    if (Object.hasOwn(rule, key) && !isWholeCount(rule[key])) {
      errors[key] = "Use a whole number from 0 to 1,000,000, or clear it.";
    }
  });
  if (
    !errors.min_n &&
    !errors.max_n &&
    isWholeCount(rule.min_n) &&
    isWholeCount(rule.max_n) &&
    rule.min_n > rule.max_n
  ) {
    errors.min_n = "Minimum units cannot exceed maximum units.";
  }
  return errors;
};

// Every invalid numeric control in the policy, keyed by its wire path
// (`default.rate`, `strata.0.target_n`, ...). Empty when the policy may be
// previewed and saved.
export const samplingPolicyErrors = (policy) => {
  const errors = {};
  if (!policy) return errors;
  const collect = (rule, prefix) =>
    Object.entries(samplingRuleErrors(rule)).forEach(([key, message]) => {
      errors[`${prefix}.${key}`] = message;
    });
  (policy.strata || []).forEach((rule, index) =>
    collect(rule, `strata.${index}`),
  );
  collect(policy.default, "default");
  return errors;
};

export const samplingStratumLabel = (identity) => {
  try {
    const [name, groups] = JSON.parse(identity);
    if (typeof name !== "string" || !Array.isArray(groups)) return identity;
    return [
      name === "default" ? "All remaining contacts" : name,
      ...groups.map((encoded) => {
        const [present, value] = JSON.parse(encoded);
        return present ? String(value) : "(missing)";
      }),
    ].join(" · ");
  } catch {
    return identity;
  }
};
