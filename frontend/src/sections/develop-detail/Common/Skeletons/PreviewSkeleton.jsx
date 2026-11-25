import { Box, Grid, Skeleton } from "@mui/material";
import React from "react";

export default function PreviewSkeleton() {
  return (
    <Box
      sx={{
        width: "100%",
        bgcolor: "background.paper",
        height: "100%",
        overflow: "auto",
        marginTop: "10px",
        borderTop: 1,
        borderColor: "divider",
      }}
    >
      <Box padding={3} height={"100%"}>
        <Grid container spacing={2} height={"100%"}>
          {/* Left Grid Skeleton */}
          <Grid
            item
            xs={8}
            sx={{
              borderRight: "2px solid grey",
              borderColor: "background.neutral",
              paddingRight: "10px",
            }}
          >
            <Box>
              {Array.from({ length: 5 }).map((_, index) => (
                <Box
                  key={index}
                  sx={{
                    marginBottom: 2,
                    padding: 2,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "6px",
                  }}
                >
                  <Skeleton
                    variant="text"
                    animation="wave"
                    width="50%"
                    height={20}
                  />
                  <Skeleton
                    variant="text"
                    animation="wave"
                    width="80%"
                    height={15}
                  />
                </Box>
              ))}
            </Box>
          </Grid>

          {/* Right Grid Skeleton */}
          <Grid item xs={4} sx={{ position: "relative" }}>
            <Box
              sx={{
                paddingBottom: "50px",
                overflowY: "auto",
                height: "calc(100% - 100px)",
              }}
            >
              {/* Annotations Header */}
              <Box
                display={"flex"}
                alignItems={"center"}
                justifyContent={"space-between"}
                sx={{ marginBottom: 2 }}
              >
                <Skeleton
                  variant="text"
                  animation="wave"
                  width="30%"
                  height={20}
                />
                <Skeleton
                  variant="rectangular"
                  animation="wave"
                  width="20%"
                  height={20}
                />
              </Box>

              {/* Annotations Skeleton */}
              {Array.from({ length: 3 }).map((_, index) => (
                <Box
                  key={index}
                  sx={{
                    marginBottom: 2,
                    padding: 2,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "6px",
                  }}
                >
                  <Skeleton
                    variant="text"
                    animation="wave"
                    width="60%"
                    height={20}
                  />
                  <Skeleton
                    variant="rectangular"
                    animation="wave"
                    width="100%"
                    height={40}
                  />
                </Box>
              ))}
            </Box>

            {/* Navigation Buttons Skeleton */}
            <Box>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  marginBottom: 3,
                }}
              >
                {Array.from({ length: 5 }).map((_, index) => (
                  <Skeleton
                    key={index}
                    variant="circular"
                    animation="wave"
                    width={24}
                    height={24}
                    sx={{ marginX: 1 }}
                  />
                ))}
              </Box>
              <Box
                sx={{
                  display: "flex",
                  gap: 2,
                  width: "100%",
                }}
              >
                <Skeleton
                  variant="rectangular"
                  animation="wave"
                  width="30%"
                  height={36}
                />
                <Skeleton
                  variant="rectangular"
                  animation="wave"
                  width="30%"
                  height={36}
                />
                <Skeleton
                  variant="rectangular"
                  animation="wave"
                  width="30%"
                  height={36}
                />
              </Box>
            </Box>
          </Grid>
        </Grid>
      </Box>
    </Box>
  );
}
