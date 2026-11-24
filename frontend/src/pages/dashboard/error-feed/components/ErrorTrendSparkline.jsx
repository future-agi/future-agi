import React, { useId, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography, useTheme } from "@mui/material";
import { format } from "date-fns";
import { seriesColor } from "src/theme/data-colors";

function toLocalDateKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function padToRange(data, days = 7) {
  if (!data?.length) return [];
  const now = new Date();
  const buckets = new Map();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
    buckets.set(toLocalDateKey(d), { timestamp: d.getTime(), value: 0 });
  }
  for (const pt of data) {
    const key = toLocalDateKey(new Date(pt.timestamp));
    if (buckets.has(key)) {
      buckets.get(key).value += Number(pt.value || 0);
    }
  }
  return Array.from(buckets.values());
}

// Smooth monotone cubic curve matching Future AGI's Tracing line sparklines
function buildSmoothPath(pts) {
  if (pts.length < 2) return "";
  let d = `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i];
    const p1 = pts[i + 1];
    const cx = ((p0.x + p1.x) / 2).toFixed(1);
    d += ` C${cx},${p0.y.toFixed(1)} ${cx},${p1.y.toFixed(1)} ${p1.x.toFixed(1)},${p1.y.toFixed(1)}`;
  }
  return d;
}

export default function ErrorTrendSparkline({
  data,
  days = 7,
  maxScale = 4,
  totalEvents,
}) {
  const theme = useTheme();
  const uid = useId();
  const [hoveredIdx, setHoveredIdx] = useState(null);

  const buckets = useMemo(() => padToRange(data, days), [data, days]);
  const sum = useMemo(
    () => totalEvents ?? buckets.reduce((acc, b) => acc + b.value, 0),
    [buckets, totalEvents],
  );

  const svgWidth = 88;
  const svgHeight = 26;
  const padX = 2;
  const padY = 3;
  const plotW = svgWidth - padX * 2;
  const plotH = svgHeight - padY * 2;
  const bottomY = padY + plotH;

  const peak = useMemo(
    () => Math.max(maxScale, ...buckets.map((b) => b.value), 1),
    [buckets, maxScale],
  );

  const points = useMemo(() => {
    if (!buckets.length) return [];
    const step = buckets.length > 1 ? plotW / (buckets.length - 1) : plotW / 2;
    return buckets.map((b, i) => ({
      x: buckets.length > 1 ? padX + i * step : padX + plotW / 2,
      y: bottomY - (b.value / peak) * plotH,
      value: b.value,
      timestamp: b.timestamp,
      label: `${format(new Date(b.timestamp), "MMM d")}: ${b.value} event${b.value === 1 ? "" : "s"}`,
    }));
  }, [buckets, plotW, plotH, bottomY, peak]);

  const linePath = useMemo(() => buildSmoothPath(points), [points]);
  const areaPath = useMemo(() => {
    if (!linePath || points.length < 2) return "";
    return `${linePath} L${points[points.length - 1].x.toFixed(1)},${bottomY} L${points[0].x.toFixed(1)},${bottomY} Z`;
  }, [linePath, points, bottomY]);

  if (!buckets.length || sum === 0) {
    return (
      <Tooltip title={`No events in last ${days} days`} arrow placement="top">
        <Typography typography="s3" color="text.secondary" sx={{ width: "fit-content" }}>
          —
        </Typography>
      </Tooltip>
    );
  }

  const strokeColor = seriesColor(theme, 0);
  const baselineColor = theme.palette.divider;
  const gradId = `ef-spark-${uid.replace(/:/g, "")}`;
  const hitW = svgWidth / Math.max(1, points.length);
  const hovered = hoveredIdx != null ? points[hoveredIdx] : null;

  return (
    <Tooltip
      title={hovered ? hovered.label : `${sum} event${sum === 1 ? "" : "s"} in last ${days}d`}
      arrow
      placement="top"
    >
      <Box
        sx={{
          width: svgWidth,
          height: svgHeight,
          position: "relative",
          display: "inline-flex",
          alignItems: "center",
        }}
        onMouseLeave={() => setHoveredIdx(null)}
      >
        <svg
          width={svgWidth}
          height={svgHeight}
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          style={{ display: "block", overflow: "visible" }}
        >
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={strokeColor} stopOpacity="0.22" />
              <stop offset="100%" stopColor={strokeColor} stopOpacity="0.01" />
            </linearGradient>
          </defs>

          {/* Subtle baseline */}
          <line
            x1={0}
            y1={bottomY}
            x2={svgWidth}
            y2={bottomY}
            stroke={baselineColor}
            strokeWidth={1}
          />

          {/* Soft gradient fill under the line */}
          {areaPath && <path d={areaPath} fill={`url(#${gradId})`} />}

          {/* Crisp Tracing-style line */}
          {linePath && (
            <path
              d={linePath}
              fill="none"
              stroke={strokeColor}
              strokeWidth={1.6}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}

          {/* Active hover dot */}
          {hovered && (
            <circle
              cx={hovered.x}
              cy={hovered.y}
              r={2.5}
              fill={strokeColor}
              stroke={theme.palette.background.paper}
              strokeWidth={1}
            />
          )}

          {/* Per-day invisible hover hit zones */}
          {points.map((pt, i) => (
            <rect
              key={pt.timestamp}
              x={i * hitW}
              y={0}
              width={hitW}
              height={svgHeight}
              fill="transparent"
              onMouseEnter={() => setHoveredIdx(i)}
            />
          ))}
        </svg>
      </Box>
    </Tooltip>
  );
}

ErrorTrendSparkline.propTypes = {
  data: PropTypes.arrayOf(
    PropTypes.shape({
      timestamp: PropTypes.number,
      value: PropTypes.number,
    }),
  ),
  days: PropTypes.number,
  maxScale: PropTypes.number,
  totalEvents: PropTypes.number,
};
