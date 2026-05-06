import { Box, Typography } from "@mui/material";
import React from "react";

const PerformanceNoData = () => {
  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        position: "relative",
        flex: 1,
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
          id="no-dataset-text"
        >
          Once you add dataset and custom metric you will be able to see
          performance metrics
        </Typography>
      </Box>
    </Box>
  );
};

export default PerformanceNoData;
