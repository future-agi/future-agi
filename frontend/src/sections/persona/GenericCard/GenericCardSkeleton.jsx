import { Box, Skeleton, useTheme } from "@mui/material";
import React from "react";

const GenericCardSkeleton = () => {
  const theme = useTheme();
  return (
    <Box
      sx={{
        padding: "1px",
        background: theme.palette.action.hover,
        borderRadius: "4px",
      }}
    >
      <Box
        sx={{
          padding: 2,
          backgroundColor: "background.paper",
          borderRadius: "4px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          height: "100%",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: "12px",
            }}
          >
            <Skeleton variant="circular" width={30} height={30} />
            <Skeleton
              variant="rectangular"
              width={200}
              height={22}
              sx={{ borderRadius: "4px" }}
            />
          </Box>
        </Box>

        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <Skeleton
            variant="rectangular"
            width="100%"
            height={44}
            sx={{ borderRadius: "4px" }}
          />
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            {Array.from({ length: 4 }).map((v) => (
              <Skeleton
                key={v}
                variant="rectangular"
                width={100}
                height={28}
                sx={{ borderRadius: "4px" }}
              />
            ))}
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

export default GenericCardSkeleton;
