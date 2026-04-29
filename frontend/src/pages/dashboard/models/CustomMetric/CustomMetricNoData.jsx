import { Box, Typography, useTheme } from "@mui/material";
import React, { useEffect } from "react";
import Xarrow, { useXarrow } from "react-xarrows";

export const CustomMetricNoData = () => {
  const updateXarrow = useXarrow();

  const theme = useTheme();

  useEffect(() => {
    updateXarrow();
  });
  return (
    <Box
      sx={{
        minHeight: "714px",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        position: "relative",
      }}
    >
      <Box
        sx={{ display: "flex", flexDirection: "column", alignItems: "center" }}
      >
        <Box
          component="img"
          alt="empty content"
          src={"/assets/icons/components/ic_extra_scroll.svg"}
          sx={{ width: 1, maxWidth: 160 }}
        />
        <Typography
          variant="subtitle1"
          sx={{ width: "250px", textAlign: "center" }}
          id="no-metric-text"
        >
          Once you create custom metric you will see it here
        </Typography>
        <Xarrow
          start="no-metric-text"
          end="add-metric-button"
          dashness
          strokeWidth={2}
          color={theme.palette.primary.main}
          startAnchor={{ position: "right", offset: { x: -50, y: 14 } }}
          endAnchor={{ position: "bottom", offset: { x: 0, y: 10 } }}
          tailShape="circle"
          showTail
          headShape="arrow1"
        />
      </Box>
    </Box>
  );
};
