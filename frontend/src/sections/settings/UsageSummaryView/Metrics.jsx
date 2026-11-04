import React from "react";
import { Skeleton } from "@mui/material";
import PropTypes from "prop-types";
import MetricStrip from "src/components/metric-strip";

const TREND = { positive: "better", negative: "worse" };

export default function Metrics({ metrics = [], isLoading = false }) {
  const items = isLoading
    ? Array.from({ length: 5 }).map((_, index) => ({
        key: `skeleton-${index}`,
        label: <Skeleton variant="text" width={80} />,
        value: <Skeleton variant="text" width={64} />,
      }))
    : (metrics || []).map((m) => ({
        key: m?.id,
        label: m?.title,
        value: m?.subtitle ? `${m?.value} ${m.subtitle}` : m?.value,
        ...(m?.percentageChange != null && {
          change: m.percentageChange,
          trend: TREND[m?.changeType] || "flat",
        }),
        hint: m?.changeLabel,
      }));

  if (items.length === 0) return null;
  return <MetricStrip items={items} />;
}

Metrics.propTypes = {
  metrics: PropTypes.array,
  isLoading: PropTypes.bool,
};
