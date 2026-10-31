import { Box, Typography } from "@mui/material";
import React from "react";
import ContentSkeleton from "src/components/content-skeleton";

const FixMyAgentLoading = () => {
  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <Box
        sx={{
          width: "47px",
          height: "47px",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          borderRadius: "50%",
          backgroundColor: "action.hover",
        }}
      >
        <ContentSkeleton variant="panel" />
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          paddingTop: "30px",
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightMedium">
          Finding suggestions to your issues..
        </Typography>
        <Typography
          sx={{ textAlign: "center", maxWidth: "376px" }}
          typography="s1"
        >
          We are analyzing your agents issues to provide solutions, this might
          take some time
        </Typography>
      </Box>
    </Box>
  );
};

export default FixMyAgentLoading;
