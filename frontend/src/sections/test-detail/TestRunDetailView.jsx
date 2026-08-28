import { Alert, Box, Button, Paper, useTheme } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import React, {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import TestRunDetailHeader from "./TestRunDetailHeader";
import { resetState, useTestDetailStore } from "./states";
import TestDetailContextProvider from "./TestDetailContextProvider";
import TestExecutionDetailTabs from "./TestExecutionDetailTabs";
import { Outlet, useLocation, useNavigate, useParams } from "react-router";
import { getTabsBasedOnAgentType } from "./common";
import useTestRunDetails from "src/hooks/useTestRunDetails";
import { AGENT_TYPES } from "../agents/constants";
import { SourceType } from "../scenarios/common";
import { useSimulationSocket } from "src/hooks/use-simulation-socket";
import axios, { endpoints } from "src/utils/axios";

const FixMyAgentDrawer = lazy(
  () => import("./FixMyAgentDrawer/FixMyAgentDrawer"),
);
const TestRunDetailView = () => {
  const theme = useTheme();

  const navigate = useNavigate();
  const { executionId, testId } = useParams();
  const { pathname } = useLocation();
  const [newExecutionId, setNewExecutionId] = useState(null);

  const { data: latestExecutionId, refetch: refetchLatestExecution } = useQuery(
    {
      queryKey: ["test-runs-latest-execution", testId],
      queryFn: () =>
        axios.get(endpoints.runTests.detailExecutions(testId), {
          params: { page: 1, limit: 1 },
        }),
      select: (response) => response.data?.results?.[0]?.id ?? null,
      enabled: !!testId,
    },
  );

  useEffect(() => {
    if (latestExecutionId && latestExecutionId !== executionId) {
      setNewExecutionId(latestExecutionId);
    } else if (latestExecutionId === executionId) {
      setNewExecutionId(null);
    }
  }, [executionId, latestExecutionId]);

  const handleSimulationUpdate = useCallback(
    (update) => {
      const updatedExecutionId = update?.test_execution_id;
      if (updatedExecutionId && updatedExecutionId !== executionId) {
        setNewExecutionId(updatedExecutionId);
      }
      refetchLatestExecution();
    },
    [executionId, refetchLatestExecution],
  );

  useSimulationSocket(testId, handleSimulationUpdate);

  const { reset } = useTestDetailStore();

  useEffect(() => {
    return () => {
      reset();
    };
  }, [reset]);

  const { data: testDetails } = useTestRunDetails(testId);
  const agentVersion = testDetails?.agent_version ?? testDetails?.agentVersion;
  const configSnapshot =
    agentVersion?.configuration_snapshot ?? agentVersion?.configurationSnapshot;
  const agentType =
    configSnapshot?.agent_type ??
    configSnapshot?.agentType ??
    AGENT_TYPES.VOICE;
  const sourceType = testDetails?.source_type ?? testDetails?.sourceType;

  // Define tabs configuration - memoized to prevent recreation
  const tabs = useMemo(
    () =>
      getTabsBasedOnAgentType({
        agentType:
          sourceType === SourceType.PROMPT ? AGENT_TYPES.CHAT : agentType,
        testId,
        executionId,
      }),
    [agentType, sourceType, testId, executionId],
  );

  // Get current tab from URL - memoized for performance
  const currentTab = useMemo(() => {
    const pathSegments = pathname.split("/");
    const lastSegment = pathSegments[pathSegments.length - 1];
    return tabs.find((tab) => tab.id === lastSegment) || tabs[0];
  }, [pathname, tabs]);

  // Handle tab change - memoized to prevent recreation
  const handleTabChange = useCallback(
    (event, newTabId) => {
      const selectedTab = tabs.find((tab) => tab.id === newTabId);
      if (selectedTab && selectedTab.path !== window.location.pathname) {
        navigate(selectedTab.path, { replace: true });
      }
    },
    [tabs, navigate],
  );

  useEffect(() => {
    return () => {
      resetState();
    };
  }, []);

  return (
    <TestDetailContextProvider>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100vh",
          backgroundColor: "background.paper",
        }}
      >
        {/* Header Section */}
        <Paper
          sx={{
            paddingX: theme.spacing(2),
            paddingTop: theme.spacing(2),
            borderRadius: 0,
            boxShadow: "none",
            backgroundColor: "background.paper",
            flexShrink: 0, // Prevent header from shrinking
          }}
        >
          <TestRunDetailHeader />
        </Paper>
        {newExecutionId && (
          <Alert
            severity="info"
            sx={{ mx: 2, mt: 1, flexShrink: 0 }}
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() =>
                  navigate(
                    `/dashboard/simulate/test/${testId}/${newExecutionId}/call-details`,
                  )
                }
              >
                Open rerun
              </Button>
            }
          >
            A newer simulation execution is available.
          </Alert>
        )}
        <TestExecutionDetailTabs
          tabs={tabs}
          currentTab={currentTab}
          onTabChange={handleTabChange}
          agentType={agentType}
        />
        <Box
          sx={{
            zIndex: 1,
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Outlet />
        </Box>
        <Suspense fallback={null}>
          <FixMyAgentDrawer />
        </Suspense>
      </Box>
    </TestDetailContextProvider>
  );
};

export default TestRunDetailView;
