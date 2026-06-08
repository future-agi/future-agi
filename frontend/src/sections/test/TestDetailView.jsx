import { Box, Paper, useTheme } from "@mui/material";
import React, { lazy, Suspense, useCallback, useEffect, useMemo } from "react";
import TestDetailHeader from "./TestDetailHeader";
import { Outlet, useLocation, useNavigate, useParams } from "react-router";
import TestDetailTabs from "./TestDetailTabs";
import Iconify from "src/components/iconify";
import { resetState, useSelectedExecutionsStore } from "./states";
import TestDetailContextProvider from "./context/TestDetailContextProvider";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const TestEvaluationDrawer = lazy(() => import("./TestEvaluationDrawer"));

const tabEventsMapper = {
  "call-logs": Events.runTestCallLogsClicked,
  runs: Events.runTestTestrunClicked,
};

const TestDetailView = () => {
  const { testId } = useParams();
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const theme = useTheme();

  // Define tabs configuration - memoized to prevent recreation
  const tabs = useMemo(
    () => [
      {
        id: "runs",
        title: "Simulated runs",
        path: `/dashboard/simulate/test/${testId}/runs`,
        icon: <Iconify icon="hugeicons:test-tube-01" />,
      },
      {
        id: "call-logs",
        title: "Logs",
        path: `/dashboard/simulate/test/${testId}/call-logs`,
        icon: <Iconify icon="tabler:logs" />,
      },
      {
        id: "analytics",
        title: "Analytics",
        path: `/dashboard/simulate/test/${testId}/analytics`,
        icon: <Iconify icon="hugeicons:analytics-01" />,
      },
    ],
    [testId],
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
        navigate(`${selectedTab.path}${search || ""}`, { replace: true });
      }
      const mixPanelEvent = tabEventsMapper[newTabId];
      if (mixPanelEvent && testId) {
        trackEvent(mixPanelEvent, {
          [PropertyName.id]: testId,
        });
      }
    },
    [tabs, navigate, search, testId],
  );

  const { selectedExecutions, setSelectedExecutions } =
    useSelectedExecutionsStore();

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
  });

  const executionsCount = useMemo(
    () => data?.pages?.[0]?.results?.length ?? data?.pages?.[0]?.count ?? null,
    [data],
  );

  const testExecutionsList = useMemo(
    () => data?.pages.flatMap((page) => page.results),
    [data],
  );

  useEffect(() => {
    if (selectedExecutions.length === 0 && testExecutionsList?.length > 0) {
      setSelectedExecutions(testExecutionsList?.slice(0, 5));
    }
  }, [testExecutionsList, setSelectedExecutions, selectedExecutions]);

  useEffect(() => {
    return () => resetState();
  }, []);

  return (
    <TestDetailContextProvider executionsCount={executionsCount}>
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
          <TestDetailHeader />
        </Paper>

        {/* Tabs Section */}
        <Paper
          sx={{
            paddingX: theme.spacing(2),
            paddingTop: theme.spacing(2),
            boxShadow: "none",
            backgroundColor: "background.paper",
            flexShrink: 0, // Prevent tabs from shrinking
          }}
        >
          <TestDetailTabs
            tabs={tabs}
            currentTab={currentTab}
            onTabChange={handleTabChange}
          />
        </Paper>

        {/* Content Section - Only this area changes when switching tabs */}
        <Box
          sx={{
            flex: 1,
            overflow: "auto", // Allow scrolling in content area
            backgroundColor: "background.paper",
          }}
        >
          <Outlet />
          <Suspense fallback={null}>
            <TestEvaluationDrawer />
          </Suspense>
        </Box>
      </Box>
    </TestDetailContextProvider>
  );
};

export default TestDetailView;
