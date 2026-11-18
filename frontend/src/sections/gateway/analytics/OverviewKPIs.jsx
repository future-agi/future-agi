import React from "react";
import PropTypes from "prop-types";
import { Skeleton } from "@mui/material";
import MetricStrip from "src/components/metric-strip";
import { useAnalyticsOverview } from "./hooks/useAnalyticsOverview";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatNumber(value) {
  if (value == null) return "--";
  const num = Number(value);
  if (Number.isNaN(num)) return "--";
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return num.toLocaleString();
}

function formatCurrency(value) {
  if (value == null) return "--";
  const num = Number(value);
  if (Number.isNaN(num)) return "--";
  if (num >= 1000) return `$${(num / 1000).toFixed(1)}K`;
  if (num >= 1) return `$${num.toFixed(2)}`;
  return `$${num.toFixed(4)}`;
}

function formatPercent(value) {
  if (value == null) return "--";
  const num = Number(value);
  if (Number.isNaN(num)) return "--";
  return `${num.toFixed(2)}%`;
}

function formatLatency(value) {
  if (value == null) return "--";
  const num = Number(value);
  if (Number.isNaN(num)) return "--";
  if (num >= 1000) return `${(num / 1000).toFixed(2)}s`;
  return `${Math.round(num)}ms`;
}

// ---------------------------------------------------------------------------
// KPI Card definitions
// ---------------------------------------------------------------------------

const KPI_CARDS = [
  {
    key: "total_requests",
    label: "Total Requests",
    formatter: formatNumber,
    invertTrend: false,
  },
  {
    key: "total_cost",
    label: "Total Cost",
    formatter: formatCurrency,
    invertTrend: true, // cost going up is "bad"
  },
  {
    key: "avg_latency_ms",
    label: "Avg Latency",
    formatter: formatLatency,
    invertTrend: true, // latency going up is "bad"
  },
  {
    key: "error_rate",
    label: "Error Rate",
    formatter: formatPercent,
    invertTrend: true, // errors going up is "bad"
  },
  {
    key: "cache_hit_rate",
    label: "Cache Hit Rate",
    formatter: formatPercent,
    invertTrend: false,
  },
];

// ---------------------------------------------------------------------------
// Trend -> metric strip change/trend (colour only on the change)
// ---------------------------------------------------------------------------

function toChange(trend, invert) {
  const n = Number(trend);
  if (trend == null || !Number.isFinite(n) || n === 0) {
    return { change: "0%", trend: "flat" };
  }
  const good = invert ? n < 0 : n > 0;
  return {
    change: `${n > 0 ? "+" : ""}${n.toFixed(1)}%`,
    trend: good ? "better" : "worse",
  };
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const OverviewKPIs = ({ start, end, gatewayId }) => {
  const { data, isLoading } = useAnalyticsOverview({ start, end, gatewayId });

  const items = KPI_CARDS.map((kpi) => ({
    key: kpi.key,
    label: kpi.label,
    value: isLoading ? (
      <Skeleton variant="text" width={64} />
    ) : (
      kpi.formatter(data?.[kpi.key]?.value)
    ),
    ...(!isLoading && toChange(data?.[kpi.key]?.trend, kpi.invertTrend)),
  }));

  return <MetricStrip items={items} />;
};

OverviewKPIs.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default OverviewKPIs;
