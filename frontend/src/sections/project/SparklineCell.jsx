import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { Line, LineChart, ResponsiveContainer } from "recharts";
import { useTheme } from "@mui/material/styles";

const SparklineCell = ({ data }) => {
  const theme = useTheme();
  const dailyVolume = data?.daily_volume || [];
  const totalVolume = data?.last_30_days_vol ?? 0;

  if (!dailyVolume.length || dailyVolume.every((v) => v === 0)) {
    return (
      <Box
        sx={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          px: 1,
          color: "text.disabled",
          fontSize: 12,
        }}
      >
        {totalVolume > 0 ? totalVolume.toLocaleString() : "-"}
      </Box>
    );
  }

  const chartData = dailyVolume.map((val, idx) => ({ day: idx, vol: val }));

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 0.5,
      }}
    >
      <Typography
        variant="body2"
        sx={{
          fontSize: 12,
          fontWeight: 500,
          color: "text.primary",
          minWidth: 32,
          flexShrink: 0,
        }}
      >
        {totalVolume.toLocaleString()}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 60, height: 32 }}>
        <ResponsiveContainer width="100%" height={32}>
          <LineChart data={chartData}>
            <Line
              type="monotone"
              dataKey="vol"
              stroke={theme.palette.primary.main}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </Box>
    </Box>
  );
};

SparklineCell.propTypes = {
  data: PropTypes.object,
};

export default React.memo(SparklineCell);
