import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { format, subDays } from "date-fns";

/**
 * Minimal bar chart for 30-day trace volume.
 * Hover shows date on the left and count on the right, below the baseline.
 */
const VolumeBarChart = ({
  dailyVolume = [],
  height = 28,
  valueSuffix = "",
}) => {
  const theme = useTheme();
  const [hoveredIdx, setHoveredIdx] = useState(null);

  const maxVal = useMemo(
    () => Math.max(...dailyVolume.map((v) => v || 0), 1),
    [dailyVolume],
  );

  const days = useMemo(() => {
    const today = new Date();
    return dailyVolume.map((val, idx) => ({
      value: val || 0,
      label: format(subDays(today, 29 - idx), "MMM d"),
    }));
  }, [dailyVolume]);

  const hovered = hoveredIdx !== null ? days[hoveredIdx] : null;

  const barColor = theme.palette.text.disabled;
  const barHoverColor = theme.palette.text.secondary;
  const lineColor = theme.palette.divider;

  return (
    <Box sx={{ width: "100%" }}>
      {/* Bars */}
      <Box
        sx={{
          height,
          display: "flex",
          alignItems: "flex-end",
          gap: "1px",
          borderBottom: `1px solid ${lineColor}`,
        }}
        onMouseLeave={() => setHoveredIdx(null)}
      >
        {days.map((day, idx) => {
          const pct =
            day.value > 0 ? Math.max((day.value / maxVal) * 100, 6) : 0;
          return (
            <Box
              key={idx}
              onMouseEnter={() => setHoveredIdx(idx)}
              sx={{
                flex: 1,
                height: `${pct}%`,
                minHeight: day.value > 0 ? 2 : 0,
                bgcolor: hoveredIdx === idx ? barHoverColor : barColor,
                borderRadius: "4px 4px 0 0",
                transition: "background-color 120ms",
                cursor: "default",
              }}
            />
          );
        })}
      </Box>

      {/* Bottom: date left, count right */}
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
            color: hovered ? "text.secondary" : "transparent",
            lineHeight: 1,
            whiteSpace: "nowrap",
          }}
        >
          {hovered ? `${hovered.value.toLocaleString()}${valueSuffix}` : ""}
        </Typography>
      </Box>
    </Box>
  );
};

VolumeBarChart.propTypes = {
  dailyVolume: PropTypes.arrayOf(PropTypes.number),
  height: PropTypes.number,
  valueSuffix: PropTypes.string,
};

export default VolumeBarChart;
