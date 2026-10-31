/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { format, subDays } from "date-fns";

/**
 * SVG line sparkline for error rate (0-100%).
 * Uses a vertical gradient: grey at 0% → red at 100%.
 * The line and area fill both transition smoothly from grey to red.
 */
const ErrorRateSparkline = ({ dailyValues = [], height = 28 }) => {
  const theme = useTheme();
  const [hoveredIdx, setHoveredIdx] = useState(null);

  const days = useMemo(() => {
    const today = new Date();
    return dailyValues.map((val, idx) => ({
      value: val || 0,
      label: format(subDays(today, dailyValues.length - 1 - idx), "MMM d"),
    }));
  }, [dailyValues]);

  const hasData = useMemo(() => days.some((d) => d.value > 0), [days]);

  const hovered = hoveredIdx !== null ? days[hoveredIdx] : null;
  const baselineColor = theme.palette.divider;

  const svgWidth = 140;
  const svgHeight = height;
  const paddingY = 2;
  const plotHeight = svgHeight - paddingY * 2;

  const points = useMemo(() => {
    if (!days.length) return [];
    const step = days.length > 1 ? svgWidth / (days.length - 1) : svgWidth / 2;
    return days.map((d, i) => {
      const x = days.length > 1 ? i * step : svgWidth / 2;
      const y = paddingY + plotHeight - (d.value / 100) * plotHeight;
      return { x, y, value: d.value };
    });
  }, [days, svgWidth, plotHeight]);

  // SVG path for the line
  const linePath = useMemo(() => {
    if (points.length < 2) return "";
    return points
      .map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`)
      .join(" ");
  }, [points]);

  // SVG path for the filled area (line + baseline closure)
  const areaPath = useMemo(() => {
    if (points.length < 2) return "";
    const bottomY = paddingY + plotHeight; // 0% line
    return `${linePath} L${points[points.length - 1].x},${bottomY} L${points[0].x},${bottomY} Z`;
  }, [linePath, points, plotHeight]);

  const hitZoneWidth = days.length > 0 ? svgWidth / days.length : 0;

  // Unique gradient ID to avoid conflicts when multiple instances render
  const gradientId = useMemo(
    () => `err-grad-${Math.random().toString(36).slice(2, 8)}`,
    [],
  );
  const fillGradientId = `${gradientId}-fill`;

  // Get interpolated color for a value (0-100): light blue → lavender → muted rose
  const getValueColor = (value) => {
    const t = Math.min(Math.max(value / 100, 0), 1);
    if (t <= 0.5) {
      const s = t * 2;
      return `rgba(${Math.round(147 + s * (196 - 147))},${Math.round(197 - s * (197 - 181))},${Math.round(253 - s * (253 - 253))},0.75)`;
    }
    const s = (t - 0.5) * 2;
    return `rgba(${Math.round(196 + s * (232 - 196))},${Math.round(181 - s * (181 - 160))},${Math.round(253 - s * (253 - 160))},0.85)`;
  };

  if (!days.length) {
    return <Box sx={{ height, width: "100%" }} />;
  }

  return (
    <Box sx={{ width: "100%" }}>
      <Box
        sx={{
          position: "relative",
          height: svgHeight,
          borderBottom: `1px solid ${baselineColor}`,
        }}
      >
        <svg
          width="100%"
          height={svgHeight}
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          preserveAspectRatio="none"
          style={{ display: "block" }}
        >
          <defs>
            {/* Vertical gradient: muted rose at top (100%) → lavender (50%) → light blue at bottom (0%) */}
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#e8a0a0" stopOpacity="0.9" />
              <stop offset="50%" stopColor="#c4b5fd" stopOpacity="0.55" />
              <stop offset="100%" stopColor="#93c5fd" stopOpacity="0.4" />
            </linearGradient>
            <linearGradient id={fillGradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#e8a0a0" stopOpacity="0.1" />
              <stop offset="40%" stopColor="#c4b5fd" stopOpacity="0.04" />
              <stop offset="100%" stopColor="#93c5fd" stopOpacity="0.01" />
            </linearGradient>
          </defs>

          {/* Filled area under the line */}
          {hasData && areaPath && (
            <path d={areaPath} fill={`url(#${fillGradientId})`} />
          )}

          {/* Faint baseline at 0% */}
          <line
            x1={0}
            y1={paddingY + plotHeight}
            x2={svgWidth}
            y2={paddingY + plotHeight}
            stroke={baselineColor}
            strokeWidth={1}
          />

          {/* The line itself */}
          {hasData && linePath && (
            <path
              d={linePath}
              fill="none"
              stroke={`url(#${gradientId})`}
              strokeWidth={1.2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}

          {/* Hover dot */}
          {hasData &&
            hoveredIdx !== null &&
            points[hoveredIdx] &&
            points[hoveredIdx].value > 0 && (
              <circle
                cx={points[hoveredIdx].x}
                cy={points[hoveredIdx].y}
                r={2.5}
                fill={getValueColor(points[hoveredIdx].value)}
              />
            )}

          {/* Invisible hit zones for hover */}
          {days.map((_, i) => (
            <rect
              key={`hit-${i}`}
              x={i * hitZoneWidth}
              y={0}
              width={hitZoneWidth}
              height={svgHeight}
              fill="transparent"
              onMouseEnter={() => setHoveredIdx(i)}
              onMouseLeave={() => setHoveredIdx(null)}
              style={{ cursor: "default" }}
            />
          ))}
        </svg>
      </Box>

      {/* Bottom: date left, value right */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          height: 14,
          mt: "2px",
        }}
      >
        <Typography
          sx={{
            fontSize: 12,
            color: hovered ? "text.secondary" : "transparent",
            lineHeight: 1,
            whiteSpace: "nowrap",
          }}
        >
          {hovered?.label ?? ""}
        </Typography>
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 500,
            color: hovered ? getValueColor(hovered.value) : "transparent",
            lineHeight: 1,
            whiteSpace: "nowrap",
          }}
        >
          {hovered ? `${hovered.value}%` : ""}
        </Typography>
      </Box>
    </Box>
  );
};

ErrorRateSparkline.propTypes = {
  dailyValues: PropTypes.arrayOf(PropTypes.number),
  height: PropTypes.number,
};

export default ErrorRateSparkline;
