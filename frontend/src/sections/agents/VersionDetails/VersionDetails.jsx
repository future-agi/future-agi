import {
  Box,
  Divider,
  useTheme,
  Tab,
  Tabs,
} from "@mui/material";
import { LoadingScreen } from "src/components/loading-screen";
import React, { useEffect } from "react";
import AgentConfigurationView from "../AgentConfiguration/AgentConfigurationView";
import CallLogsView from "../CallLogs/CallLogsView";
import WorkflowView from "../Workflow/WorkflowView";
import { AGENT_TAB_IDS, AGENT_TYPES } from "../constants";
import { useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { icon } from "../helper";
import { useAgentDetailsStore } from "../store/agentDetailsStore";
import { useUrlState } from "src/routes/hooks/use-url-state";
import PerformanceAnalyticsView from "../PerformanceAnalytics/PerformanceAnalyticsView";

const VersionDetails = () => {
  const theme = useTheme();
  const { agentDefinitionId } = useParams();
  const [activeTab, setActiveTab] = useUrlState(
    "activeTab",
    AGENT_TAB_IDS.AGENT_CONFIGURATION,
  );
  const { setAgentDetails, setAgentName, selectedVersion, resetAgentDetails } =
    useAgentDetailsStore();

  const { data: versionDetails, isLoading } = useQuery({
    queryKey: ["agentVersionDetail", agentDefinitionId, selectedVersion],
    queryFn: async () => {
      const res = await axios.get(
        endpoints.agentDefinitions.versionDetail(
          agentDefinitionId,
          selectedVersion,
        ),
      );
      return res.data;
    },
    enabled:
      !!agentDefinitionId && !!selectedVersion && selectedVersion !== "null",
  });
  const agentType =
    versionDetails?.configuration_snapshot?.agent_type ?? AGENT_TYPES.VOICE;

  useEffect(() => {
    if (versionDetails) {
      setAgentDetails(versionDetails);
      setAgentName(versionDetails?.configuration_snapshot?.agent_name || "");
    }
  }, [versionDetails, setAgentDetails, setAgentName]);

  const renderContent = (activeTab) => {
    switch (activeTab) {
      case AGENT_TAB_IDS.PERFORMANCE_ANALYTICS:
        return <PerformanceAnalyticsView />;
      case AGENT_TAB_IDS.CALL_LOGS:
        return <CallLogsView agentType={agentType} />;
      case AGENT_TAB_IDS.WORKFLOW:
        return <WorkflowView />;
      default:
        return <AgentConfigurationView />;
    }
  };

  const agentDetailsTabData = [
    {
      id: "AgentConfiguration",
      title: "Agent Configuration",
      icon: () => icon("agent_configuration"),
    },
    {
      id: "PerformanceAnalytics",
      title: "Performance Analytics",
      icon: () => icon("performance_analytics"),
    },
    {
      id: "CallLogs",
      title: agentType === AGENT_TYPES.CHAT ? "Chat Logs" : "Call Logs",
      icon: () => icon("call_logs"),
    },
    // {
    //   id: "Workflow",
    //   title: "Workflow",
    //   icon: () => icon("workflow"),
    // },
  ];

  const handleTabChange = (_, newValue) => {
    setActiveTab(newValue);
  };

  useEffect(() => {
    return () => {
      resetAgentDetails();
    };
  }, [resetAgentDetails]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      {/* <AgentHeader /> */}
      <Divider sx={{ borderColor: "divider" }} />

      <Box
        sx={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
      >
        <Tabs
          value={activeTab || agentDetailsTabData[0].id}
          onChange={handleTabChange}
          textColor="primary"
          TabIndicatorProps={{
            style: {
              backgroundColor: theme.palette.primary.main,
            },
          }}
          sx={{
            bgcolor: "background.paper",
            borderBottom: 1,
            borderColor: "divider",
            minHeight: 42,
            "& .MuiTabs-flexContainer": { gap: 0 },
            "& .MuiTab-root": {
              minHeight: 42,
              paddingX: theme.spacing(3),
              margin: theme.spacing(0),
              marginRight: `${theme.spacing(0)} !important`,
              minWidth: "auto",
              fontWeight: "fontWeightMedium",
              typography: "s1",
              color: "text.primary",
              textTransform: "none",
              transition: theme.transitions.create(
                ["color", "background-color"],
                {
                  duration: theme.transitions.duration.short,
                },
              ),
              "&.Mui-selected": {
                color: "primary.main",
              },
              "&:not(.Mui-selected)": { color: "text.primary" },
              "&:first-of-type": { marginLeft: 0 },
            },
          }}
        >
          {agentDetailsTabData.map((tab) => (
            <Tab
              key={tab.id}
              label={tab.title}
              icon={tab.icon()}
              value={tab.id}
              iconPosition="start"
            />
          ))}
        </Tabs>

        {/* Scrollable content */}
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", mt: 2 }}>
          {isLoading || !versionDetails ? (
            <LoadingScreen variant="orbit" sx={{ height: "100%", minHeight: "50vh" }} />
          ) : (
            renderContent(activeTab)
          )}
        </Box>
      </Box>
    </Box>
  );
};

export default VersionDetails;
