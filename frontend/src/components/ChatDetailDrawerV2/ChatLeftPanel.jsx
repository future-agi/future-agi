import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import CompactTabs from "src/components/VoiceDetailDrawerV2/CompactTabs";
import PathAnalysisView from "src/components/VoiceDetailDrawerV2/PathAnalysisView";
import { ShowComponent } from "src/components/show";
import LoadingStateComponent from "src/components/CallLogsDetailDrawer/LoadingStateComponent";
import { getLoadingStateWithRespectiveStatus } from "src/sections/test-detail/common";
import ChatTranscriptView from "./ChatTranscriptView";

const TABS = {
  TRANSCRIPT: "transcript",
  CHECKLIST: "checklist",
  GRAPH: "graph",
};

// Chat drawer left panel — Transcript / Checklist / Graph (mirrors VoiceLeftPanel).
const ChatLeftPanel = ({ data, scenarioId }) => {
  const isSimulate = data?.module === "simulate";
  const [currentTab, setCurrentTab] = useState(TABS.TRANSCRIPT);
  /* The Expected steps tab: its list or its graph. */
  const [pathView, setPathView] = useState("checklist");

  const { isCallInProgress, message: loadingMessage } =
    getLoadingStateWithRespectiveStatus(
      data?.status,
      data?.simulation_call_type || data?.simulationCallType,
    );

  const showPathTabs = isSimulate && !!data?.id;

  const tabs = useMemo(() => {
    const t = [
      {
        label: "Transcript",
        value: TABS.TRANSCRIPT,
        icon: "solar:document-text-linear",
      },
    ];
    if (showPathTabs) {
      t.push({
        /* Checklist and Graph are one view of the expected steps — one tab,
           with a List / Graph switch inside (drawer audit, 12 Nov). */
        label: "Expected steps",
        value: TABS.CHECKLIST,
        icon: "solar:list-check-linear",
      });
    }
    return t;
  }, [showPathTabs]);

  const isTranscriptTab = currentTab === TABS.TRANSCRIPT;
  const isPathTab = currentTab === TABS.CHECKLIST || currentTab === TABS.GRAPH;

  return (
    <Stack
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <Box sx={{ px: 1.25, flexShrink: 0 }}>
        <CompactTabs
          value={currentTab}
          onChange={(_, value) => setCurrentTab(value)}
          tabs={tabs}
        />
      </Box>

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          px: 1.25,
          pt: 1,
          pb: 1,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <ShowComponent condition={isCallInProgress}>
          <LoadingStateComponent message={loadingMessage} />
        </ShowComponent>

        <ShowComponent condition={!isCallInProgress}>
          <>
            <ShowComponent condition={isTranscriptTab}>
              <ChatTranscriptView data={data} />
            </ShowComponent>
            <ShowComponent condition={isPathTab && showPathTabs}>
              <PathAnalysisView
                data={data}
                scenarioId={scenarioId}
                openedExecutionId={data?.id}
                enabled={isPathTab}
                viewMode={pathView}
                onViewModeChange={setPathView}
                analysisOnRequest={isSimulate}
                onRequestTranscript={() => setCurrentTab(TABS.TRANSCRIPT)}
              />
            </ShowComponent>
          </>
        </ShowComponent>
      </Box>
    </Stack>
  );
};

ChatLeftPanel.propTypes = {
  data: PropTypes.object.isRequired,
  scenarioId: PropTypes.string,
};

export default ChatLeftPanel;
