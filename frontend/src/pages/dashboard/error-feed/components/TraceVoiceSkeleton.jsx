import React from "react";
import { Skeleton, Stack } from "@mui/material";

const TraceVoiceSkeleton = () => (
  <Stack gap={1} sx={{ p: 1 }}>
    <Skeleton variant="rounded" width="40%" height={28} />
    <Skeleton variant="rounded" width="100%" height={70} />
    <Skeleton variant="rounded" width="100%" height={70} />
    <Stack gap={0.5} sx={{ mt: 1 }}>
      {[0, 1, 2, 3].map((i) => (
        <Skeleton
          key={i}
          variant="text"
          width={i % 2 ? "80%" : "60%"}
          height={20}
        />
      ))}
    </Stack>
  </Stack>
);

export default TraceVoiceSkeleton;
