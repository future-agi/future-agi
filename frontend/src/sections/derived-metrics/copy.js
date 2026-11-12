import { formatMetricTime } from "./metricTime";

/** Semantic copy, not example measurements. Missing inputs never mean zero. */
export const metricCopy = {
  noValue: "No value",
  empty: "No data",
  emptyText: "Empty text",
  inProgress: "In progress",
  notCalculated: "Not calculated yet",
  loading: "Loading metric value…",
  onDemand: "Calculating for this window…",
  stale: "Stale value",
  error: "Could not load metric value",
  unsupported: "Metric value unavailable",
  sampledInputs:
    "Estimate not available: user metrics need every conversation evaluated.",
  estimate: "Estimate",
  match: "Counts",
  other: "Doesn't count",
  missingExplanation:
    "Missing values are not zero and do not mean “Doesn't count”.",
  estimateExplanation:
    "A weighted estimate. The interval covers sampling error only, not evaluator error.",
  staleExplanation:
    "This is the last returned value, not a newly calculated result.",
};

export const noValueReasons = {
  not_evaluated: "not evaluated",
  attribute_absent: "attribute absent",
  missing_inputs: "missing inputs",
  zero_denominator: "zero denominator",
  invalid_probability: "invalid sampling probability",
  non_finite: "not a finite number",
  retention_expired: "outside data retention",
  unknown_anchor: "unit timing unavailable",
  deleted: "data deleted",
};

const intervalReasons = {
  no_value: "point value unavailable",
  design_not_captured: "sampling design not captured",
  mixed_design: "mixed sampling designs",
  unweighted_total: "an exact total has no sampling interval",
  fewer_than_two_units: "fewer than two evaluated units",
  population_size_missing: "population size missing",
  inconsistent_stratum_design: "inconsistent sampling design",
  single_unit_stratum: "only one evaluated unit in a sampling group",
  no_degrees_of_freedom: "insufficient degrees of freedom",
  non_finite: "not a finite number",
  unstable_variance: "insufficient support for a reliable interval",
  not_computed: "not computed",
};

/** A metric on a project above the backfill size limit starts with no history. */
export const forwardCoverageCopy = (coverageStart) =>
  `Values from ${formatMetricTime(coverageStart)}. History isn't computed for projects this large yet.`;

export function noValueCopy(reason) {
  return Object.hasOwn(noValueReasons, reason)
    ? `${metricCopy.noValue}: ${noValueReasons[reason]}`
    : metricCopy.noValue;
}

export function intervalUnavailableCopy(reason) {
  const explanation = Object.hasOwn(intervalReasons, reason)
    ? intervalReasons[reason]
    : "reason unavailable";
  return `Interval unavailable: ${explanation}`;
}

/** Counts are returned diagnostics, never reconstructed from a displayed rate. */
export function metricCountCopy(value, unitNoun, locale) {
  const number = (n) => new Intl.NumberFormat(locale).format(n);
  const isCount = (n) => Number.isSafeInteger(n) && n >= 0;
  const lines = [];
  if (isCount(value?.counted_users) && isCount(value?.eligible_users)) {
    lines.push(
      `${number(value.counted_users)} of ${number(value.eligible_users)} users`,
    );
  } else if (
    isCount(value?.evaluated_count) &&
    isCount(value?.eligible_count)
  ) {
    lines.push(
      unitNoun
        ? `from ${number(value.evaluated_count)} evaluated of ${number(value.eligible_count)} ${unitNoun}`
        : `${number(value.evaluated_count)} evaluated · ${number(value.eligible_count)} eligible`,
    );
  }
  if (
    isCount(value?.missing_count) &&
    value.missing_count > 0 &&
    !isCount(value?.no_value_users)
  ) {
    lines.push(`${number(value.missing_count)} with no value`);
  }
  if (isCount(value?.no_value_users)) {
    lines.push(`${number(value.no_value_users)} users with no value`);
  }
  if (
    isCount(value?.conversations_without_user) &&
    value.conversations_without_user > 0
  ) {
    lines.push(
      `${number(value.conversations_without_user)} conversations without a single user`,
    );
  }
  return lines;
}

/** One wording for a window the active definition has not calculated, whether
 * a read or a snapshot capture ran into it. */
export const windowNotCalculatedCopy =
  "This window has not been calculated. Choose a later start date.";

/** Dates/counts are supplied by callers from the snapshot contract, not inferred. */
export function snapshotStateCopy(state, { date, removedCount, locale } = {}) {
  switch (state) {
    case "saved":
      return "Snapshot saved";
    case "changed":
      if (date && Number.isSafeInteger(removedCount) && removedCount >= 0) {
        return `Changed ${date}: ${new Intl.NumberFormat(locale).format(removedCount)} conversations removed after a data erasure request`;
      }
      return "Snapshot changed after a data erasure request";
    case "expired":
      return "Snapshot not saved: the data changed before it could be copied. Try again.";
    case "deleted":
      return "Snapshot deleted";
    default:
      return "Snapshot unavailable";
  }
}
