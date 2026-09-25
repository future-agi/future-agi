import _ from "lodash";

const yAxisUnitGroups = {
  Count: [
    "default",
    "count_of_errors",
    "token_usage",
    "daily_tokens_spent",
    "monthly_tokens_spent",
  ],
  ms: ["latency", "span_response_time", "llm_response_time"],
  $: ["cost"],
  "%": [
    "llm_api_failure_rates",
    "error_rates_for_function_calling",
    "error_free_session_rates",
    "service_provider_error_rates",
    "evaluation_metrics",
  ],
};

export function getYAxisUnit(key) {
  for (const unit in yAxisUnitGroups) {
    if (yAxisUnitGroups[unit].includes(key)) {
      return unit;
    }
  }
  return "Count"; // fallback
}

export const formatYAxisValue = (value, selectedGraphProperty) => {
  const key = _.toLower(selectedGraphProperty);

  const formatRules = {
    cost: (v) => Number(v).toFixed(6),
  };

  const formatter = formatRules[key] || ((v) => v.toString());
  return formatter(value);
};

const AVG_SYSTEM_METRICS = ["cost"];
// Every Observe latency series is the t-digest median (p50), never a mean.
const MEDIAN_SYSTEM_METRICS = ["latency"];

export const getLineSeriesName = (selectedGraphProperty) => {
  if (!selectedGraphProperty) return "";

  const baseUnit =
    getYAxisUnit(_.toLower(selectedGraphProperty)) || getYAxisUnit("default");

  const isAvgMetric = AVG_SYSTEM_METRICS.includes(
    _.toLower(selectedGraphProperty),
  );
  const isMedianMetric = MEDIAN_SYSTEM_METRICS.includes(
    _.toLower(selectedGraphProperty),
  );
  const prefix = isMedianMetric ? "Median " : isAvgMetric ? "Avg. " : "";

  const label = `${prefix}${_.capitalize(selectedGraphProperty)}`;

  return `${baseUnit} (${label})`;
};
