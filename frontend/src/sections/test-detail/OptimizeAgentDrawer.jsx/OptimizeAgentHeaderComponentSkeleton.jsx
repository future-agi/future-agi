import React from "react";
import { Box, Skeleton, Stack } from "@mui/material";

const OptimizeAgentHeaderComponentSkeleton = () => {
  return (
    <Stack spacing={2}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Skeleton variant="text" width={200} height={28} />
      </Box>

      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          {/* Optimizer Used Chip Skeleton */}
          <Skeleton
            variant="rectangular"
            width={180}
            height={32}
            sx={{ borderRadius: 1 }}
          />

          {/* Model Chip Skeleton */}
          <Skeleton
            variant="rectangular"
            width={100}
            height={32}
            sx={{ borderRadius: "4px" }}
          />

          {/* CallStatus Skeleton */}
          <Skeleton
            variant="rectangular"
            width={90}
            height={32}
            sx={{ borderRadius: 1 }}
          />
        </Box>

        {/* Right Section Button Skeleton */}
        <Box sx={{ display: "flex", gap: 1 }}>
          <Skeleton
            variant="rectangular"
            width={160}
            height={32}
            sx={{ borderRadius: 1 }}
          />
        </Box>
      </Box>
    </Stack>
  );
};

export default OptimizeAgentHeaderComponentSkeleton;
