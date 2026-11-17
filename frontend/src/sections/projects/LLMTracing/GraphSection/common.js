import { formatExactMoney } from "src/sections/common/format/money";
import { formatUsdCost } from "src/utils/format-usd-cost";
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

/** Optional generated monetary companion; absence keeps legacy presentation. */
export const isIncompleteCost = (coverage) =>
  Boolean(coverage && coverage.status !== "complete");

/**
 * `known_subtotal` arrives as a JSON float (a ClickHouse Float64 sum) or as a
 * client-side reduce over such floats, so it carries no authoritative decimal
 * digits: 0.0005734000000000001 is binary noise. Twelve significant digits
 * drop that noise and keep every cent and sub-cent a price can produce; the
 * shared USD formatter then prints it like every other cost.
 */
export const formatKnownSubtotal = (subtotal) =>
  formatUsdCost(
    typeof subtotal === "number" && Number.isFinite(subtotal)
      ? Number(subtotal.toPrecision(12))
      : subtotal,
  );

/** A known subtotal must never be labelled as a complete total or mean. */
export const monetaryCostLabel = (value, coverage) => {
  if (coverage?.status === "unknown") return "Unknown";
  if (coverage?.status === "partial") {
    return `Partial · Known subtotal ${formatKnownSubtotal(coverage.known_subtotal)}`;
  }
  return value == null ? "—" : `$${value}`;
};
