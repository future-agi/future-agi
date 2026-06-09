import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CompactTabs from "./CompactTabs";
import { ShowComponent } from "src/components/show";
import LoadingStateComponent from "src/components/CallLogsDetailDrawer/LoadingStateComponent";
import { getLoadingStateWithRespectiveStatus } from "src/sections/test-detail/common";
import PathAnalysisView from "./PathAnalysisView";
import TranscriptView from "./TranscriptView";
import VoiceAudioBridge from "./VoiceAudioBridge";

const TABS = {
  TRANSCRIPT: "transcript",
  CHECKLIST: "checklist",
  GRAPH: "graph",
};

const PATH_VIEW_MODE = {
  [TABS.CHECKLIST]: "checklist",
  [TABS.GRAPH]: "graph",
};

/**
 * Voice drawer left panel. Top-level tabs:
 *
 *   • Transcript — shows the recording waveform at the top and the
 *     transcript list below. The recording only renders in this tab so
 *     the other three path-analysis tabs get the full panel height.
 *   • Checklist / Timeline / Graph — each is a view mode of the shared
 *     `PathAnalysisView`. Lifting them to the top-level tab bar gives
 *     every view much more vertical space than the nested toggle did.
 *
 * The `VoiceAudioBridge` stays mounted across tab switches (wrapped in
 * a hidden container on non-transcript tabs) so seeking via click from
 * a path view keeps the audio player hydrated.
 */
const VoiceLeftPanel = ({ data, scenarioId, embedded = false }) => {
  const isSimulate = data?.module === "simulate";
  const [currentTab, setCurrentTab] = useState(TABS.TRANSCRIPT);

  const { isCallInProgress, message: loadingMessage } =
    getLoadingStateWithRespectiveStatus(
      data?.status,
      data?.simulation_call_type,
    );

  const filteredTranscript = useMemo(() => {
    const transcript = data?.transcript;
    return transcript?.filter((item) => item.speakerRole !== "system");
  }, [data]);

  const showPathTabs = isSimulate && !!data?.id;

  const tabs = useMemo(() => {
    const t = [
      {
        label: "Transcript",
        value: TABS.TRANSCRIPT,
        icon: "mdi:file-document-outline",
      },
    ];
    if (showPathTabs) {
      t.push({
        label: "Checklist",
        value: TABS.CHECKLIST,
        icon: "mdi:format-list-checks",
      });
      t.push({
        label: "Graph",
        value: TABS.GRAPH,
        icon: "mdi:graph-outline",
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
      {/* Tabs at the top — no chrome above them so all four views get
          the full panel height. Hidden in `embedded` mode (e.g. error-feed
          Overview) where the parent SectionCard already provides chrome. */}
      {!embedded && (
        <Box sx={{ px: 1.25, flexShrink: 0 }}>
          <CompactTabs
            value={currentTab}
            onChange={(_, value) => setCurrentTab(value)}
            tabs={tabs}
          />
        </Box>
      )}

      {/* Scrollable tab content. `embedded` callers drop internal padding so
          the panel sits flush inside their own container. */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          px: embedded ? 0 : 1.25,
          pt: embedded ? 0 : 1,
          pb: embedded ? 0 : 1,
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
              <Stack gap={1} sx={{ minHeight: 0, flex: 1 }}>
                {/* Recording lives inside the Transcript tab content so
                    it only takes space when the user is on this tab.
                    Audio state survives tab switches because
                    voiceAudioStore queues pending seeks for the next
                    mount — see store.seekTo().

                    When there is no transcript the Recording block grows
                    to fill the panel so its centered empty-state message
                    ("No recording found …") sits in the middle instead
                    of being pinned to the top. */}
                <Box
                  sx={{
                    flexShrink: 0,
                    ...(filteredTranscript?.length
                      ? {}
                      : {
                          flex: 1,
                          minHeight: 0,
                          display: "flex",
                          flexDirection: "column",
                          justifyContent: "center",
                        }),
                  }}
                >
                  <ShowComponent
                    condition={!!filteredTranscript?.length && !embedded}
                  >
                    <Typography
                      sx={{
                        fontSize: 10,
                        fontWeight: 600,
                        color: "text.secondary",
                        textTransform: "uppercase",
                        letterSpacing: "0.04em",
                        mb: 0.5,
                      }}
                    >
                      Recording
                    </Typography>
                  </ShowComponent>
                  <VoiceAudioBridge data={data} />
                </Box>
                <ShowComponent condition={!!filteredTranscript?.length}>
                  <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
                    <TranscriptView
                      transcript={filteredTranscript}
                      embedded={embedded}
                    />
                  </Box>
                </ShowComponent>
              </Stack>
            </ShowComponent>
            <ShowComponent condition={isPathTab && showPathTabs}>
              <PathAnalysisView
                data={data}
                scenarioId={scenarioId}
                openedExecutionId={data?.id}
                enabled={isPathTab}
                viewMode={PATH_VIEW_MODE[currentTab]}
                onRequestTranscript={() => setCurrentTab(TABS.TRANSCRIPT)}
              />
            </ShowComponent>
          </>
        </ShowComponent>
      </Box>
    </Stack>
  );
};

VoiceLeftPanel.propTypes = {
  data: PropTypes.object.isRequired,
  scenarioId: PropTypes.string,
  embedded: PropTypes.bool,
};

export default VoiceLeftPanel;
