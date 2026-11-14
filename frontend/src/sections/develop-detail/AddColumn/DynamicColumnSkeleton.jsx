import { Box, Skeleton } from "@mui/material";
import React from "react";

const DynamicColumnSkeleton = () => {
  return (
    <Box
      sx={{
        padding: 2,
        height: "100%",
        flex: 1,
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Skeleton variant="text" width={150} height={32} />
        <Skeleton variant="circular" width={16} height={16} />
      </Box>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2, flex: 1 }}>
        <Skeleton variant="rounded" sx={{ borderRadius: "4px" }} height={32} />
        <Skeleton variant="rounded" sx={{ borderRadius: "4px" }} height={32} />
        <Skeleton variant="rounded" sx={{ borderRadius: "4px" }} height={70} />
        <Skeleton variant="rounded" sx={{ borderRadius: "4px" }} height={32} />
      </Box>
      <Box sx={{ display: "flex", gap: 1 }}>
        <Skeleton
          variant="rounded"
          sx={{ borderRadius: "4px" }}
          height={28}
          width="100%"
        />
        <Skeleton
          variant="rounded"
          sx={{ borderRadius: "4px" }}
          height={28}
          width="100%"
        />
      </Box>
    </Box>
  );
};

export default DynamicColumnSkeleton;
