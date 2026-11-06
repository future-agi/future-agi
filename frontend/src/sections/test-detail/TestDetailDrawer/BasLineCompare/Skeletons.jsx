import React from "react";
import {
  Box,
  Grid,
  Paper,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableRow,
  useTheme,
} from "@mui/material";
import { StyledBox } from "src/sections/projects/SessionsView/ReplaySessions/CreateScenariosForm";

export const HeaderSkeleton = () => {
  return (
    <Stack gap={0} height="67px" justifyContent="center">
      <Skeleton variant="text" width="180px" height="24px" sx={{ mb: 0.5 }} />
      <Skeleton variant="text" width="320px" height="18px" />
    </Stack>
  );
};

export const PerformanceMetricsSkeleton = () => {
  return (
    <StyledBox
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        height: "184px",
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Skeleton variant="text" width="180px" height="28px" />
        <Skeleton variant="circular" width="24px" height="24px" />
      </Stack>

      <Grid container spacing={1}>
        {[1, 2, 3, 4].map((item) => (
          <Grid item xs={12} sm={6} md={3} key={item}>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "4px",
                bgcolor: "background.paper",
                minHeight: "102px",
                padding: 1.5,
              }}
            >
              <Stack direction="column" gap={0.5}>
                <Skeleton variant="text" width="80%" height="18px" />
                <Skeleton variant="text" width="60%" height="24px" />
                <Skeleton variant="text" width="90%" height="16px" />
              </Stack>
            </Box>
          </Grid>
        ))}
      </Grid>
    </StyledBox>
  );
};

export const CompareConversationSkeleton = () => {
  const _theme = useTheme();
  return (
    <Stack direction="column" gap={2}>
      {/* Header with title and controls */}
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Skeleton variant="text" width="220px" height="28px" />
        <Stack direction="row" alignItems="center" gap={1}>
          <Skeleton
            variant="rectangular"
            width="120px"
            height="32px"
            sx={{ borderRadius: 1 }}
          />
          <Skeleton variant="circular" width="16px" height="16px" />
        </Stack>
      </Stack>

      {/* Table Container */}
      <TableContainer
        component={Paper}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          boxShadow: "none",
          height: "400px",
          overflow: "hidden",
        }}
      >
        <Table sx={{ tableLayout: "fixed" }}>
          <TableBody>
            {/* Header Row */}
            <TableRow>
              <TableCell
                sx={{
                  width: "50%",
                  padding: 2,
                  borderRight: "1px solid",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  backgroundColor: "background.default",
                }}
              >
                <Stack direction="row" alignItems="center" gap={1}>
                  <Skeleton
                    variant="rectangular"
                    width="24px"
                    height="24px"
                    sx={{ borderRadius: "4px" }}
                  />
                  <Skeleton variant="text" width="180px" height="20px" />
                </Stack>
              </TableCell>
              <TableCell
                sx={{
                  width: "50%",
                  padding: 2,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  backgroundColor: "background.default",
                }}
              >
                <Stack direction="row" alignItems="center" gap={1}>
                  <Skeleton
                    variant="rectangular"
                    width="24px"
                    height="24px"
                    sx={{ borderRadius: "4px" }}
                  />
                  <Skeleton variant="text" width="180px" height="20px" />
                </Stack>
              </TableCell>
            </TableRow>

            {/* Content Row */}
            <TableRow>
              {/* Baseline Column */}
              <TableCell
                sx={{
                  width: "50%",
                  padding: 1.5,
                  borderRight: (theme) =>
                    `1px solid ${theme.palette.divider || "divider"} !important`,
                  verticalAlign: "top",
                  backgroundColor: (theme) =>
                    theme.palette.background.paper || "background.default",
                }}
              >
                <Stack gap={2}>
                  {[1, 2, 3, 4].map((item) => (
                    <Box key={item}>
                      <Skeleton
                        variant="text"
                        width="30%"
                        height="16px"
                        sx={{ mb: 0.5 }}
                      />
                      <Skeleton
                        variant="rectangular"
                        width="100%"
                        height="60px"
                        sx={{ borderRadius: 1 }}
                      />
                    </Box>
                  ))}
                </Stack>
              </TableCell>

              {/* Replayed Column */}
              <TableCell
                sx={{
                  width: "50%",
                  padding: 1.5,
                  verticalAlign: "top",
                  backgroundColor: "background.paper",
                }}
              >
                <Stack gap={2}>
                  {[1, 2, 3, 4].map((item) => (
                    <Box key={item}>
                      <Skeleton
                        variant="text"
                        width="30%"
                        height="16px"
                        sx={{ mb: 0.5 }}
                      />
                      <Skeleton
                        variant="rectangular"
                        width="100%"
                        height="60px"
                        sx={{ borderRadius: 1 }}
                      />
                    </Box>
                  ))}
                </Stack>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </TableContainer>
    </Stack>
  );
};

export const TestDetailDrawerScenarioTableSkeleton = () => {
  return (
    <Box sx={{ marginX: 2 }}>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          backgroundColor: "background.default",
          px: 2,
          py: 1.5,
          display: "flex",
          flexDirection: "column",
          gap: 1,
          height: "288px",
        }}
      >
        {/* Header */}
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Skeleton variant="text" width="140px" height="28px" />
          <Skeleton variant="circular" width="24px" height="24px" />
        </Stack>

        {/* Content */}
        <Box
          sx={{
            border: "1px solid var(--border-default)",
            backgroundColor: "background.paper",
            padding: "21px",
            position: "relative",
          }}
        >
          {/* Scenario Section */}
          <Box mb={2}>
            <Skeleton
              variant="text"
              width="80px"
              height="18px"
              sx={{ mb: 0.5 }}
            />
            <Skeleton variant="text" width="90%" height="20px" />
          </Box>

          {/* Horizontal Scrollable Cards */}
          <Box
            sx={{
              display: "flex",
              gap: 1.5,
              overflowX: "hidden",
            }}
          >
            {[1, 2, 3].map((item) => (
              <Box
                key={item}
                sx={{
                  width: "400px",
                  minHeight: "115px",
                  flexShrink: 0,
                  display: "flex",
                  flexDirection: "column",
                }}
              >
                <Skeleton
                  variant="text"
                  width="120px"
                  height="18px"
                  sx={{ mb: 0.5 }}
                />
                <Skeleton variant="text" width="100%" height="20px" />
                <Skeleton variant="text" width="80%" height="20px" />
                <Skeleton variant="text" width="60%" height="20px" />
              </Box>
            ))}
          </Box>

          {/* View full details button */}
          <Box
            sx={{
              position: "absolute",
              right: 21,
              bottom: 21,
              display: "flex",
              alignItems: "center",
              gap: 0.5,
            }}
          >
            <Skeleton variant="circular" width="16px" height="16px" />
            <Skeleton variant="text" width="100px" height="16px" />
          </Box>
        </Box>
      </Box>
    </Box>
  );
};
