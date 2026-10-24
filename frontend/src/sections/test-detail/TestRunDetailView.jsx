import { Box, useTheme } from "@mui/material";
import Section from "src/components/section";
import React, { lazy, Suspense, useCallback, useEffect, useMemo } from "react";
import TestRunDetailHeader from "./TestRunDetailHeader";
import { resetState, useTestDetailStore } from "./states";
import TestDetailContextProvider from "./TestDetailContextProvider";
import TestExecutionDetailTabs from "./TestExecutionDetailTabs";
import { Outlet, useLocation, useNavigate, useParams } from "react-router";
import { getTabsBasedOnAgentType } from "./common";
import useTestRunDetails from "src/hooks/useTestRunDetails";
import { AGENT_TYPES } from "../agents/constants";
import { SourceType } from "../scenarios/common";

const FixMyAgentDrawer = lazy(
  () => import("./FixMyAgentDrawer/FixMyAgentDrawer"),
);
const TestRunDetailView = () => {
  const theme = useTheme();

  const navigate = useNavigate();
  const { executionId, testId } = useParams();
  const { pathname } = useLocation();

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
        <Section
          sx={{
            paddingX: theme.spacing(2),
            paddingTop: theme.spacing(2),
            flexShrink: 0,
          }}
        >
          <TestRunDetailHeader />
        </Section>
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
