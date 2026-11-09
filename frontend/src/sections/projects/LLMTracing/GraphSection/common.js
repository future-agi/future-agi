import { formatExactMoney } from "src/sections/common/format/money";
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
    cost: (v) => formatExactMoney(v, { prefix: "", minimumFractionDigits: 6 }),
  };

  const formatter = formatRules[key] || ((v) => v.toString());
  return formatter(value);
};

const AVG_SYSTEM_METRICS = ["cost", "latency"];

export const getLineSeriesName = (selectedGraphProperty) => {
  if (!selectedGraphProperty) return "";

  const baseUnit =
    getYAxisUnit(_.toLower(selectedGraphProperty)) || getYAxisUnit("default");

  const isAvgMetric = AVG_SYSTEM_METRICS.includes(
    _.toLower(selectedGraphProperty),
  );

  const label = `${isAvgMetric ? "Avg. " : ""}${_.capitalize(
    selectedGraphProperty,
  )}`;

  return `${baseUnit} (${label})`;
};
