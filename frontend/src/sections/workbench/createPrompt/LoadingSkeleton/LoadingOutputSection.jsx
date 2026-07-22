import { Box, Skeleton } from "@mui/material";
import React from "react";

const LoadingOutputSection = () => {
  return (
    <Box sx={{ height: "100%" }}>
      <Skeleton
        variant="rectangular"
        height={"100%"}
        sx={{ borderRadius: "8px" }}
      />
    </Box>
  );
};

export default LoadingOutputSection;
