import { Box, Button, Tooltip, Typography, useTheme } from "@mui/material";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import { useSelectedExecutionsStore } from "../states";
import CompareDatasetSummaryIcon from "src/sections/develop-detail/DatasetSummaryTab/CompareDatasetSummaryIcon";
import TestAnalyticsEvalsCard from "./TestAnalyticsEvalsCard";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useNavigate, useParams } from "react-router";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";

const TestAnalyticsView = () => {
  const theme = useTheme();

  const { selectedExecutions } = useSelectedExecutionsStore();
  const [currentExecution, setCurrentExecution] = useState(null);
  const { testId } = useParams();
  const { role } = useAuthContext();

  const { data } = useInfiniteQuery({
    queryFn: async ({ pageParam = 1 }) => {
      const response = await axios.get(
        endpoints.runTests.detailExecutions(testId),
        {
          params: {
            page: pageParam,
            search: "",
          },
        },
      );
      return response.data;
    },
    queryKey: ["test-runs-executions", testId, ""],
    staleTime: Infinity,
    getNextPageParam: ({ next, current_page }) =>
      next ? current_page + 1 : null,
    initialPageParam: 1,
    enabled: false,
  });

  const isTestExecutions = useMemo(() => {
    const testExecutions = data?.pages.flatMap((page) => page.results);
    return testExecutions?.length > 0;
  }, [data]);

  const navigate = useNavigate();

  const isCompare = currentExecution === null;

  if (!isTestExecutions) {
    return (
      <EmptyLayout
        title="No Test Executions Found"
        description="You need to run a test to see the analytics"
        action={
          <Button
            variant="contained"
            onClick={() => {
              navigate(`/dashboard/simulate/test/${testId}/runs`);
            }}
            sx={{
              bgcolor: "primary.main",
              "&:hover": {
                bgcolor: "primary.dark",
              },
            }}
            disabled={
              !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
            }
          >
            Run New Test
          </Button>
        }
        hideIcon
      />
    );
  }

  return (
    <Box
      sx={{
        padding: 2,
        display: "flex",
        gap: 2,
        height: "100%",
      }}
    >
      <Box
        sx={{
          width: "17%",
          borderRight: "1px solid var(--border-light)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          overflow: "auto",
          paddingRight: 1,
          gap: 1,
        }}
      >
        <Box sx={{ width: "100%" }}>
          <Button
            startIcon={
              <Iconify
                // @ts-ignore
                icon="solar:transfer-horizontal-linear"
                style={{
                  color: isCompare
                    ? theme.palette.primary.main
                    : theme.palette.text.disabled,
                }}
              />
            }
            sx={{
              width: "100%",
              backgroundColor: isCompare
                ? theme.palette.action.hover
                : "background.paper",
              color: isCompare
                ? theme.palette.primary.main
                : theme.palette.text.primary,
              justifyContent: "flex-start",
              "&:hover": { backgroundColor: theme.palette.action.hover },
            }}
            onClick={() => {
              setCurrentExecution(null);
            }}
          >
            Compare
          </Button>
          {selectedExecutions?.map((selectedExecution, index) => {
            return (
              <Box
                key={index}
                sx={{
                  display: "flex",
                  gap: 1.6,
                  marginTop: "18px",
                  backgroundColor:
                    currentExecution === index
                      ? "action.hover"
                      : "background.paper",
                  cursor: "pointer",
                  padding: "6px",
                  borderRadius: "6px",
                  "&:hover": { backgroundColor: "action.selected" },
                  alignItems: "center",
                }}
                onClick={() => {
                  setCurrentExecution(index);
                }}
              >
                <CompareDatasetSummaryIcon index={index} />
                <Tooltip title={`Execution: ${selectedExecution.id}`}>
                  <Typography
                    typography="s1"
                    fontWeight={"fontWeightRegular"}
                    color={"text.primary"}
                    sx={{
                      display: "inline-block",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    Execution: {selectedExecution.id.slice(0, 8)}
                  </Typography>
                </Tooltip>
              </Box>
            );
          })}
        </Box>
      </Box>
      <TestAnalyticsEvalsCard selectedIndex={currentExecution} />
    </Box>
  );
};

export default TestAnalyticsView;
