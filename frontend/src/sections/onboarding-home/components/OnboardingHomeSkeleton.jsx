import React from "react";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";

export default function OnboardingHomeSkeleton() {
  return (
    <Box
      data-testid="onboarding-home-skeleton"
      sx={{
        width: "100%",
        minHeight: "calc(100vh - 120px)",
        bgcolor: "background.paper",
        p: { xs: 2, md: 3 },
      }}
    >
      <Stack spacing={3} sx={{ maxWidth: 1180, mx: "auto" }}>
        <Stack spacing={1}>
          <Skeleton variant="text" width={160} height={24} />
          <Skeleton variant="text" width="min(100%, 520px)" height={44} />
          <Skeleton variant="text" width="min(100%, 680px)" height={24} />
        </Stack>

        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "minmax(0, 2fr) 1fr" },
            gap: 2,
          }}
        >
          <Box
            sx={{
              minHeight: 220,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 2,
            }}
          >
            <Stack spacing={2}>
              <Skeleton variant="text" width={220} height={28} />
              <Skeleton variant="text" width="92%" height={22} />
              <Skeleton variant="text" width="68%" height={22} />
              <Skeleton variant="rounded" width={160} height={40} />
            </Stack>
          </Box>
          <Box
            sx={{
              minHeight: 220,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 2,
            }}
          >
            <Stack spacing={2}>
              <Skeleton variant="text" width={180} height={28} />
              <Skeleton variant="rounded" height={40} />
              <Skeleton variant="rounded" height={40} />
              <Skeleton variant="rounded" height={40} />
            </Stack>
          </Box>
        </Box>

        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))" },
            gap: 1.5,
          }}
        >
          {[0, 1].map((item) => (
            <Skeleton key={item} variant="rounded" height={92} />
          ))}
        </Box>
      </Stack>
    </Box>
  );
}
