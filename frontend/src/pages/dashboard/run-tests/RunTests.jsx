import { Box } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useLocation } from "react-router";
import CreateRunTestPage from "src/components/run-tests/CreateRunTestPage";
import RunTestsContent from "src/components/run-tests/RunTestsContent";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildVoiceRouteFocusPayload,
  getVoiceOnboardingParams,
  voiceSetupQuickStartAttributionFromSearch,
  VOICE_ONBOARDING_MODES,
} from "src/sections/test/onboardingVoiceRouteEvents";

function RunTests() {
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const gridRef = useRef(null);
  const location = useLocation();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const recordedFocusRef = useRef(false);
  const voiceParams = useMemo(
    () => getVoiceOnboardingParams(location.search),
    [location.search],
  );
  const voiceQuickStartAttribution = useMemo(
    () => voiceSetupQuickStartAttributionFromSearch(location.search),
    [location.search],
  );
  const isCreateTestCallMode =
    voiceParams.mode === VOICE_ONBOARDING_MODES.CREATE_TEST_CALL;

  useEffect(() => {
    if (!isCreateTestCallMode) return;
    setCreateDialogOpen(true);
    if (recordedFocusRef.current) return;
    recordedFocusRef.current = true;
    recordActivationEvent?.(
      buildVoiceRouteFocusPayload({
        mode: voiceParams.mode,
        quickStartAttribution: voiceQuickStartAttribution,
        source: "voice_simulation_create",
        agentDefinitionId: voiceParams.agentDefinitionId,
      }),
    );
  }, [
    isCreateTestCallMode,
    recordActivationEvent,
    voiceQuickStartAttribution,
    voiceParams.agentDefinitionId,
    voiceParams.mode,
  ]);

  const handleCreateSuccess = () => {
    setCreateDialogOpen(false);
    gridRef.current?.api?.refreshServerSide({ purge: true });
  };

  return (
    <>
      <Helmet>
        <title>Run Tests | Dashboard</title>
      </Helmet>

      {createDialogOpen ? (
        <CreateRunTestPage
          open={createDialogOpen}
          onClose={handleCreateSuccess}
          initialAgentDefinitionId={voiceParams.agentDefinitionId}
          initialAgentType={
            isCreateTestCallMode && voiceParams.agentDefinitionId
              ? AGENT_TYPES.VOICE
              : null
          }
        />
      ) : (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            height: "100vh",
            p: 2,
          }}
        >
          <RunTestsContent
            showHeader={true}
            showSearch={true}
            onCreateClick={() => setCreateDialogOpen(true)}
            gridRef={gridRef}
          />
        </Box>
      )}
    </>
  );
}

export default RunTests;
