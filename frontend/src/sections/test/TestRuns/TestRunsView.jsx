import { Box } from "@mui/material";
import React, { useEffect } from "react";
import TestRunHeader from "./TestRunHeader";
import TestRunsGrid from "./TestRunsGrid";
import { resetState } from "./states";
import { useParams } from "react-router";
import { ShowComponent } from "src/components/show";
import SDkComponentVoiceTestRun from "./SDkComponentVoiceTestRun";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { useSimulationSocket } from "src/hooks/use-simulation-socket";
import { useTestDetailContext } from "../context/TestDetailContext";

const TestRunsView = () => {
  useEffect(() => {
    return () => resetState();
  }, []);
  const { testId } = useParams();
  const { testData, refreshTestRunGrid, executionsCount } =
    useTestDetailContext();

  // Refresh data when the backend sends a WebSocket notification.
  useSimulationSocket(testId, refreshTestRunGrid);

  const agentType =
    testData?.agent_version?.configuration_snapshot?.agent_type ??
    testData?.agentVersion?.configurationSnapshot?.agentType;
  const sourceType = testData?.source_type ?? testData?.sourceType;
  const showSdk = executionsCount === 0 && agentType === AGENT_TYPES.CHAT;

  return (
    <Box
      sx={{
        padding: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
      }}
    >
      <ShowComponent condition={showSdk}>
        <SDkComponentVoiceTestRun agentType={agentType} />
      </ShowComponent>

      <ShowComponent condition={!showSdk}>
        <TestRunHeader />
        <TestRunsGrid agentType={agentType} simulationType={sourceType} />
      </ShowComponent>
    </Box>
  );
};

export default TestRunsView;
