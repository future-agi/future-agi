import { Box, Typography, useTheme } from "@mui/material";
import React, { useEffect } from "react";
import Xarrow, { useXarrow } from "react-xarrows";

const NoMetricAdded = () => {
  const updateXarrow = useXarrow();

  const theme = useTheme();

  useEffect(() => {
    updateXarrow();
  });

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        flex: 1,
        width: "100%",
      }}
    >
      <Xarrow
        start="optimize-no-metric"
        end="optimize-no-metric-add"
        dashness
        strokeWidth={2}
        color={theme.palette.primary.main}
        startAnchor={{ position: "right", offset: { x: -80, y: 10 } }}
        endAnchor={{ position: "bottom", offset: { x: 5, y: 130 } }}
        tailShape="circle"
        showTail
        headShape="arrow1"
      />
      <Typography
        sx={{ width: "30ch" }}
        textAlign="center"
        fontSize="13px"
        fontWeight={600}
        id="optimize-no-metric"
      >
        Choose metrics from the dropdown and click ‘+’
      </Typography>
    </Box>
  );
};

export default NoMetricAdded;
