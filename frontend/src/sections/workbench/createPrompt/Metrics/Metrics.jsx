import { Box, Tab, Tabs, useTheme } from "@mui/material";
import React, { useMemo } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import WorkbenchMetricsProvider from "./context/WorkbenchMetricsProvider";
import { useWorkbenchMetrics } from "./context/WorkbenchMetricsContext";
import MetricsContent from "./MetricsContent/MetricsContent";
import LinkedTracesContent from "./LinkedTracesContent/LinkedTracesContent";
import MetricFilterDrawer from "./MetricFilterDrawer/MetricFilterDrawer";
import { getMetricsTabSx } from "./common";
import { METRIC_TAB_IDS } from "./constants";
import SvgColor from "src/components/svg-color";
import { paths } from "src/routes/paths";
import { enqueueSnackbar } from "src/components/snackbar";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildPromptFirstQualityLoopCompletedPayload,
  getPromptOnboardingRouteParams,
  PROMPT_ONBOARDING_MODES,
} from "../promptActions/promptOnboardingRoute";
import PromptMetricsOnboardingFocusPanel from "./PromptMetricsOnboardingFocusPanel";

const icon = (name) => (
  <SvgColor
    src={`/assets/icons/workbench_metrics/${name}.svg`}
    sx={{ width: 20, height: 20 }}
  />
);

const MetricsTabs = () => {
  const theme = useTheme();
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const completePromptLoop = useRecordActivationEvent();
  const { activeTab, setActiveTab, setIsFilterDrawerOpen } =
    useWorkbenchMetrics();
  const promptOnboardingParams = getPromptOnboardingRouteParams(searchParams);
  const isMetricsOnboarding =
    promptOnboardingParams.isOnboarding &&
    promptOnboardingParams.mode === PROMPT_ONBOARDING_MODES.METRICS;

  const metricsTabData = [
    { id: "Metrics", title: "Metrics", icon: () => icon("metric") },
    {
      id: "LinkedTraces",
      title: "Linked Traces",
      icon: () => icon("linked_traces"),
    },
  ];

  const tabSx = useMemo(() => getMetricsTabSx(theme), [theme]);

  const handleTabChange = (_, newValue) => {
    setActiveTab(newValue);
  };

  const handleCompletePromptLoop = async () => {
    try {
      await completePromptLoop.mutateAsync(
        buildPromptFirstQualityLoopCompletedPayload({
          promptId: id,
        }),
      );
      navigate(
        `${paths.dashboard.home}?mode=daily-quality&source=onboarding&target_event=first_quality_loop_completed`,
        { replace: true },
      );
    } catch {
      enqueueSnackbar("Unable to finish prompt loop. Please try again.", {
        variant: "error",
      });
    }
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Tabs
        value={activeTab || metricsTabData[0].id}
        onChange={handleTabChange}
        textColor="primary"
        TabIndicatorProps={{
          style: {
            backgroundColor: theme.palette.primary.main,
          },
        }}
        sx={tabSx}
      >
        {metricsTabData.map((tab) => (
          <Tab
            key={tab.id}
            icon={tab.icon()}
            label={tab.title}
            value={tab.id}
            iconPosition="start"
          />
        ))}
      </Tabs>

      <Box sx={{ flex: 1, minHeight: 0, mt: 2 }}>
        {activeTab === METRIC_TAB_IDS.METRICS && (
          <MetricsContent isOnboarding={isMetricsOnboarding} />
        )}
        {activeTab === METRIC_TAB_IDS.LINKED_TRACES && (
          <LinkedTracesContent isOnboarding={isMetricsOnboarding} />
        )}
      </Box>

      <PromptMetricsOnboardingFocusPanel
        activeTab={activeTab}
        isCompletingLoop={completePromptLoop.isPending}
        isOnboarding={isMetricsOnboarding}
        onCompleteLoop={handleCompletePromptLoop}
        onOpenFilters={() => setIsFilterDrawerOpen(true)}
        onOpenLinkedTraces={() => setActiveTab(METRIC_TAB_IDS.LINKED_TRACES)}
      />
    </Box>
  );
};

const Metrics = () => {
  return (
    <WorkbenchMetricsProvider>
      <Box
        flex={1}
        overflow="hidden"
        paddingX={2}
        display="flex"
        flexDirection="column"
        minHeight={0}
      >
        <MetricsTabs />
        <MetricFilterDrawer />
      </Box>
    </WorkbenchMetricsProvider>
  );
};

export default Metrics;
