import { Box, Tab, Tabs, useTheme } from "@mui/material";
import React, { useMemo } from "react";
import WorkbenchMetricsProvider from "./context/WorkbenchMetricsProvider";
import { useWorkbenchMetrics } from "./context/WorkbenchMetricsContext";
import MetricsContent from "./MetricsContent/MetricsContent";
import LinkedTracesContent from "./LinkedTracesContent/LinkedTracesContent";
import { getMetricsTabSx } from "./common";
import { METRIC_TAB_IDS } from "./constants";
import SvgColor from "src/components/svg-color";

const icon = (name) => (
  <SvgColor
    src={`/assets/icons/workbench_metrics/${name}.svg`}
    sx={{ width: 20, height: 20 }}
  />
);

const MetricsTabs = () => {
  const theme = useTheme();
  const { activeTab, setActiveTab } = useWorkbenchMetrics();

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

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Tabs
        value={activeTab || metricsTabData[0].id}
        onChange={handleTabChange}
        textColor="primary"
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
        {activeTab === METRIC_TAB_IDS.METRICS && <MetricsContent />}
        {activeTab === METRIC_TAB_IDS.LINKED_TRACES && <LinkedTracesContent />}
      </Box>
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
      </Box>
    </WorkbenchMetricsProvider>
  );
};

export default Metrics;
