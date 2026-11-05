import { intervalUnavailableCopy, metricCopy } from "./copy";

function numericFormat(value, metadata, locale) {
  // The field catalog's percent format is the projection contract. Points also
  // carry a rate proof: weighting or an observed 0..1 range is never that proof.
  const percent =
    metadata.value_format === "percent" &&
    (!Object.hasOwn(metadata, "rate_eligible") ||
      metadata.rate_eligible === true);
  const options = percent ? { style: "percent" } : {};
  const magnitude = percent ? value * 100 : value;
  if (Number.isInteger(magnitude)) options.maximumFractionDigits = 0;
  else options.maximumSignificantDigits = 6;
  const formatted = new Intl.NumberFormat(locale, options).format(
    value === 0 ? 0 : value,
  );
  const unit = metadata.unit;
  // Never introduce a percent through a suffix after rejecting its rate proof.
  return !percent && unit && unit !== "%" && unit !== "percent"
    ? `${formatted} ${unit}`
    : formatted;
}

/**
 * Format a scalar using server-declared metadata. Returns null for missing or
 * mismatched values so callers must choose a labelled state, never a fake zero.
 * MetricFieldMetadataSerializer supplies value_type; points are numeric.
 */
export function formatMetricValue(value, metadata = {}, locale) {
  if (value == null) return null;
  if (metadata.projection === true && metadata.value_type !== "number")
    return null;
  switch (metadata.value_type) {
    case "number":
      return typeof value === "number" && Number.isFinite(value)
        ? numericFormat(value, metadata, locale)
        : null;
    case "boolean":
      if (typeof value !== "boolean") return null;
      if (value) return metadata.value_labels?.match || metricCopy.match;
      return metadata.value_labels?.other || metricCopy.other;
    case "text":
      if (typeof value !== "string") return null;
      return value === "" ? metricCopy.emptyText : value;
    default:
      return null;
  }
}

/** Bounds stay asymmetric and in the point's own units; no guessed margin. */
export function formatMetricInterval(point, locale) {
  const interval = point?.uncertainty;
  if (
    interval?.status !== "ok" ||
    !Number.isFinite(interval.ci_low) ||
    !Number.isFinite(interval.ci_high) ||
    interval.ci_low > interval.ci_high
  ) {
    return intervalUnavailableCopy(interval?.reason);
  }
  const metadata = { ...point, value_type: "number" };
  const low = formatMetricValue(interval.ci_low, metadata, locale);
  const high = formatMetricValue(interval.ci_high, metadata, locale);
  const confidence = interval.confidence_level;
  const label =
    typeof confidence === "number" && confidence > 0 && confidence < 1
      ? `${new Intl.NumberFormat(locale, { style: "percent", maximumSignificantDigits: 6 }).format(confidence)} interval`
      : "Interval";
  return `${label}: ${low} – ${high}`;
}
