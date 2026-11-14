import { Box, Skeleton } from "@mui/material";
import React from "react";

function PropmtCardSkeleton() {
  return (
    <Box
      sx={{
        p: "24px",
        borderRadius: "12px",
        border: "1px solid var(--border-default)",
        mb: "29px",
      }}
    >
      <Skeleton variant="text" width="60%" height={40} />
      <Skeleton variant="rectangular" height={350} sx={{ mt: 2 }} />
    </Box>
  );
}

export default PropmtCardSkeleton;
