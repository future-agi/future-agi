import React from "react";
import { Box, Divider, Skeleton, useTheme } from "@mui/material";

const VersionsListSkeleton = () => {
  const theme = useTheme();

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(1),
          p: theme.spacing(1.5),
          px: theme.spacing(2.5),
        }}
      >
        <Skeleton variant="text" width="180px" height={28} />
        <Skeleton
          variant="rectangular"
          width="100%"
          height={35}
          sx={{ borderRadius: 1 }}
        />
      </Box>

      <Divider sx={{ borderColor: "divider" }} />

      {/* Scrollable versions list */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          "&::-webkit-scrollbar": { width: "6px" },
          "&::-webkit-scrollbar-thumb": {
            backgroundColor: "border.strong",
            borderRadius: "4px",
          },
          "&::-webkit-scrollbar-track": { backgroundColor: "transparent" },
          px: theme.spacing(1),
          py: 1,
        }}
      >
        {Array.from({ length: 5 }).map((_, index) => (
          <Box
            key={index}
            sx={{
              position: "relative",
              mb: 2,
              pl: 1,
              "&::after": {
                content: '""',
                position: "absolute",
                left: 18,
                top: 30,
                bottom: -22,
                width: "2px",
                backgroundColor: "action.disabledBackground",
                display: index === 4 ? "none" : "block",
              },
            }}
          >
            {/* FormControlLabel equivalent structure */}
            <Box
              sx={{
                display: "flex",
                alignItems: "flex-start",
                m: 0,
              }}
            >
              {/* Radio button skeleton */}
              <Box sx={{ pt: 0.8, pr: 1 }}>
                <Skeleton variant="circular" width={20} height={20} />
              </Box>

              {/* Label content skeleton */}
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.5,
                  py: 0.8,
                  width: "100%",
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Skeleton variant="text" width="150px" height={24} />
                </Box>

                {/* Timestamp */}
                <Skeleton variant="text" width="80px" height={16} />

                {/* Description */}
                <Skeleton variant="text" width="90%" height={20} />
                <Skeleton variant="text" width="90%" height={20} />
              </Box>
            </Box>
          </Box>
        ))}
      </Box>
    </Box>
  );
};

export default VersionsListSkeleton;
