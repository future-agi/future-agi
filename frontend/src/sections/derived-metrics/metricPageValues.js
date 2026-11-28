import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import { windowNotCalculatedCopy, windowNotCalculatedYetCopy } from "./copy";
import { formatMetricValue } from "./formatMetricValue";
import { formatMetricTime, forwardValuesStart } from "./metricTime";
import { pointValueState } from "./unitValueState";

// A returned difference is in metric units, not relative percent change.
export function formatMetricDifference(value, point) {
  if (!Number.isFinite(value) || !point) return null;
  const rate = point.rate_eligible === true && point.value_format === "percent";
  const formatted = formatMetricValue(rate ? value * 100 : value, {
    ...point,
    value_type: "number",
    value_format: "number",
    unit: rate ? "percentage points" : point.unit,
  });
  return formatted == null ? null : `${value > 0 ? "+" : ""}${formatted}`;
}

export function sameMetricRead(first, second) {
  return Boolean(
    first &&
      second &&
      first.provenance.manifest_id === second.provenance.manifest_id &&
      first.provenance.version_id === second.provenance.version_id &&
      first.provenance.generation === second.provenance.generation &&
      first.effective_window.window_start ===
        second.effective_window.window_start &&
      first.effective_window.window_end === second.effective_window.window_end,
  );
}

/** Fields do not carry a version. Verify all display semantics against the
 * definition pinned by the driver read before applying its type or labels. */
export function matchingMetricDriverField(field, definition) {
  if (!field || !definition || field.projection !== false) return false;
  if (
    !["kind", "level", "value_type"].every(
      (key) => field[key] === definition[key],
    )
  )
    return false;
  if (
    !["value_format", "unit"].every(
      (key) => (field[key] || "") === (definition[key] || ""),
    )
  )
    return false;
  if (field.value_type !== "boolean") return true;
  const actual = field.value_labels || {};
  const expected = definition.value_labels || {};
  return (
    Object.keys(actual).length === Object.keys(expected).length &&
    Object.keys(expected).every((key) => actual[key] === expected[key])
  );
}

export function metricTrendPoints(results) {
  return results.map((point) => ({
    x: Date.parse(point.window_start),
    y: pointValueState(point).state === "value" ? point.value : null,
  }));
}

/** Metrics are on but their ClickHouse schema is not installed yet. */
export const METRIC_SCHEMA_NOT_INSTALLED =
  "Metrics schema not installed. An administrator needs to install it before metrics can be calculated or read.";

const unavailableMessages = {
  metric_schema_not_installed: METRIC_SCHEMA_NOT_INSTALLED,
  user_reads_unavailable: "User-level metrics are not available yet.",
  user_projection_unavailable:
    "User-level metric values are not available yet.",
  span_evidence_unavailable:
    "Span drivers and unit evidence are not available yet. KPI and trend remain available.",
  metric_drivers_unavailable:
    "Drivers are not available for distinct counts or percentiles yet.",
  retention_expired:
    "Evidence is outside data retention. Aggregate values may still be available.",
  metric_recalculating:
    "Not calculated yet. A completed active definition is not available.",
  window_not_calculated: windowNotCalculatedCopy,
  window_not_calculated_yet: windowNotCalculatedYetCopy(),
  metric_version_changed:
    "The active definition changed. Reload the metric to read the new version.",
};

/** A failed action (queue a recalculation, save or delete a snapshot) says
 * what did not happen, not that something could not load. Wire codes keep
 * their specific copy; a validation message the server marks safe is shown
 * as is. */
export function metricActionMessage(error, failure) {
  if (Object.hasOwn(unavailableMessages, error?.code))
    return unavailableMessages[error.code];
  if (error?.statusCode === 403)
    return "You do not have permission to change this metric.";
  if (error?.statusCode === 404)
    return "This metric is not available in this workspace.";
  if (error?.statusCode === 429)
    return `${failure} The service is busy. Try again shortly.`;
  return getSafeActionErrorMessage(error, `${failure} Try again.`);
}

/** Error codes are part of the wire contract. Never turn a failed read into no data.
 * The read's definition, when known, keeps "not calculated yet" true for a
 * metric that started with no history: it says where its values begin. */
export function metricReadMessage(error, label, definition) {
  const first = forwardValuesStart(definition);
  if (error?.code === "metric_recalculating" && first)
    return `Not calculated yet. Values from ${formatMetricTime(first)} appear after the next calculation.`;
  if (error?.code === "window_not_calculated_yet")
    return windowNotCalculatedYetCopy(error.calculated_through, error.reason);
  return (
    unavailableMessages[error?.code] ||
    (error?.statusCode === 403
      ? "You do not have permission to view this metric."
      : error?.statusCode === 404
        ? "This metric is not available in this workspace."
        : `Could not load ${label.toLowerCase()}. Retry or reload the metric.`)
  );
}
