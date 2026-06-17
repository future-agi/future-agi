import React from "react";
import PropTypes from "prop-types";
import { Box, Card, Grid, Typography, Skeleton, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import { useTheme } from "@mui/material/styles";
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
// Trend indicator
// ---------------------------------------------------------------------------

function TrendIndicator({ trend, invert }) {
  const theme = useTheme();

  if (trend == null || trend === 0) {
    return (
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Iconify
          icon="mdi:trending-neutral"
          width={16}
          sx={{ color: theme.palette.text.secondary }}
        />
        <Typography variant="caption" color="text.secondary">
          0%
        </Typography>
      </Stack>
    );
  }

  const isPositiveDirection = trend > 0;
  // When inverted (cost, latency, errors), positive trend is bad
  const isGood = invert ? !isPositiveDirection : isPositiveDirection;
  const color = isGood ? theme.palette.success.main : theme.palette.error.main;

  const trendIcon = isPositiveDirection
    ? "mdi:trending-up"
    : "mdi:trending-down";

  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <Iconify icon={trendIcon} width={16} sx={{ color }} />
      <Typography variant="caption" sx={{ color, fontWeight: 600 }}>
        {isPositiveDirection ? "+" : ""}
        {Number(trend).toFixed(1)}%
      </Typography>
    </Stack>
  );
}

TrendIndicator.propTypes = {
  trend: PropTypes.number,
  invert: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Single KPI card
// ---------------------------------------------------------------------------

function KPICard({ label, value, trend, formatter, invertTrend, isLoading }) {
  if (isLoading) {
    return (
      <Card sx={{ p: 3 }}>
        <Skeleton variant="text" width="60%" height={20} />
        <Skeleton variant="text" width="40%" height={36} sx={{ mt: 1 }} />
        <Skeleton variant="text" width="30%" height={16} sx={{ mt: 0.5 }} />
      </Card>
    );
  }

  return (
    <Card sx={{ p: 3 }}>
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ fontWeight: 500, mb: 1 }}
      >
        {label}
      </Typography>
      <Typography variant="h4" sx={{ fontWeight: 700 }}>
        {formatter(value)}
      </Typography>
      <Box mt={0.5}>
        <TrendIndicator trend={trend} invert={invertTrend} />
      </Box>
    </Card>
  );
}

KPICard.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  trend: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  formatter: PropTypes.func.isRequired,
  invertTrend: PropTypes.bool,
  isLoading: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const OverviewKPIs = ({ start, end, gatewayId }) => {
  const { data, isLoading } = useAnalyticsOverview({ start, end, gatewayId });

  return (
    <Grid container spacing={2}>
      {KPI_CARDS.map((kpi) => (
        <Grid item xs={12} sm={6} md key={kpi.key}>
          <KPICard
            label={kpi.label}
            value={data?.[kpi.key]?.value}
            trend={data?.[kpi.key]?.trend}
            formatter={kpi.formatter}
            invertTrend={kpi.invertTrend}
            isLoading={isLoading}
          />
        </Grid>
      ))}
    </Grid>
  );
};

OverviewKPIs.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default OverviewKPIs;
