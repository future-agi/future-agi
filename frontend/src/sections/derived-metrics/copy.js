import {
  firstValuesInstant,
  formatMetricClock,
  formatMetricTime,
} from "./metricTime";

/** Semantic copy, not example measurements. Missing inputs never mean zero. */
export const metricCopy = {
  noValue: "No value",
  empty: "No data",
  emptyText: "Empty text",
  inProgress: "In progress",
  notCalculated: "Not calculated yet",
  settling: "Not final yet: conversations still open",
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

/** A metric on a project above the backfill size limit starts with no
 * history. Its values begin at the first whole 15-minute period. */
export const forwardCoverageCopy = (coverageStart) =>
  `Values from ${formatMetricTime(firstValuesInstant(coverageStart))}. History isn't computed for projects this large yet.`;

/** Where a forward metric's calculated values stop, when a window runs past
 * it: nothing starting at or after it is calculated yet. */
export const calculatedThroughCopy = (through) =>
  `Final through ${formatMetricTime(through)}. Later values are not calculated yet or still settling.`;

/** Why a project's metrics stopped calculating (calculation_paused). Values
 * already calculated stay; nothing new is calculated until the limit is
 * raised. */
export const calculationPausedCopy = (paused) => {
  if (paused?.reason !== "too_large") return "";
  const rows = Number(paused.rows).toLocaleString("en-US");
  const limit = Number(paused.limit).toLocaleString("en-US");
  return `Calculation paused: this project stores ${rows} metric rows, above the limit of ${limit}. Values calculated so far stay available; newer data is not calculated until the limit is raised.`;
};

/** A window with no final values yet: past where values are calculated
 * (not_calculated), calculated but still holding open conversations
 * (settling), or past where a paused project stopped (too_large). ``through``
 * is where final values stop. */
export const windowNotCalculatedYetCopy = (through, reason) => {
  const why =
    reason === "settling"
      ? "Not final yet: conversations in this window are still open."
      : reason === "too_large"
        ? "Not calculated: this project's metrics are paused because it is too large."
        : "Not calculated yet.";
  // A paused project has no next calculation until its limit is raised.
  const next =
    reason === "too_large"
      ? "Choose an earlier window."
      : "Choose an earlier window or check back after the next calculation.";
  return through
    ? `${why} Values are final through ${formatMetricTime(through)}. ${next}`
    : `${why} ${next}`;
};

/** A window that holds no whole 15-minute period of a forward metric's values
 * yet (coverageReadableAt says when it will). */
export function forwardPendingCopy(definition, now = new Date()) {
  const first = firstValuesInstant(definition?.coverage_start);
  if (!first) return "Values are not calculated yet.";
  const readable = new Date(first.getTime() + 15 * 60000);
  return now < readable
    ? `Values start at ${formatMetricClock(first)}. They appear here after ${formatMetricClock(readable)}, when that first 15-minute period ends.`
    : `This window ends before ${formatMetricTime(readable)}, when the first 15-minute period of values ended. Choose a window that ends later.`;
}

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
