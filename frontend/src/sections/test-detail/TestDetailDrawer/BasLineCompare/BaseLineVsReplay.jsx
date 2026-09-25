import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import CompareHeaderBar from "src/components/BaselineCompare/CompareHeaderBar";
import CompareMetrics from "src/components/BaselineCompare/CompareMetrics";
import CompareScenarioSummary from "src/components/BaselineCompare/CompareScenarioSummary";
import CompareSectionLabel from "src/components/BaselineCompare/CompareSectionLabel";
import CompareTranscript from "src/components/BaselineCompare/CompareTranscript";
import { StereoMultiTrackPlayer } from "../AudioPlayerCustom";
import { transformToConversations } from "./common";

const toPlayerRecordings = (rec) => {
  if (!rec) return null;
  return {
    stereo: rec.stereo || "",
    assistant: rec.mono_assistant || "",
    customer: rec.mono_customer || "",
    combined: rec.mono_combined || "",
    mono: rec.mono_combined || "",
  };
};

const RecordingPlayer = ({ label, recordings, id }) => {
  const mapped = toPlayerRecordings(recordings);
  const hasAudio = mapped?.stereo || mapped?.combined || mapped?.assistant;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        bgcolor: "background.paper",
        p: 1.25,
        minWidth: 0,
      }}
    >
      <Typography
        sx={{
          fontSize: 11,
          fontWeight: 700,
          color: "text.primary",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          mb: 1,
        }}
      >
        {label}
      </Typography>
      {hasAudio ? (
        <Box sx={{ minWidth: 0, overflowX: "auto" }}>
          <StereoMultiTrackPlayer recordings={mapped} id={id} height={50} />
        </Box>
      ) : (
        <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
          No recording available
        </Typography>
      )}
    </Box>
  );
};

RecordingPlayer.propTypes = {
  label: PropTypes.string.isRequired,
  recordings: PropTypes.object,
  id: PropTypes.string,
};

export default function BaseLineVsReplay({ rowData, onBack }) {
  const { data: baselineVsReplayData, isLoading: isLoadingBaselineVsReplay } =
    useQuery({
      queryKey: ["baseline-vs-replay", rowData?.id],
      queryFn: () => {
        return axios.get(
          endpoints.testExecutions.compareExecutions(rowData?.id),
        );
      },
      select: (response) => response.data.result,
      enabled: !!rowData?.id,
    });

  const recordings = baselineVsReplayData?.comparison_recordings;

  const transcripts = useMemo(() => {
    if (!baselineVsReplayData?.comparison_transcripts) return null;
    return transformToConversations(
      baselineVsReplayData.comparison_transcripts,
    );
  }, [baselineVsReplayData?.comparison_transcripts]);

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        width: "100%",
        overflow: "auto",
      }}
    >
      <CompareHeaderBar
        onBack={onBack}
        scenarioName={rowData?.scenario}
        sessionId={rowData?.session_id ?? rowData?.sessionId}
        backLabel="Back to call"
      />

      <Stack gap={2} sx={{ p: 1.5 }}>
        <CompareMetrics
          data={baselineVsReplayData?.comparison_metrics}
          isLoading={isLoadingBaselineVsReplay}
          simulationCallType={rowData?.simulation_call_type}
        />

        <CompareScenarioSummary data={rowData} />

        {recordings && (
          <Stack gap={0.75}>
            <CompareSectionLabel>Call recordings</CompareSectionLabel>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "minmax(0, 1fr)",
                  md: "minmax(0, 1fr) minmax(0, 1fr)",
                },
                gap: 1,
              }}
            >
              <RecordingPlayer
                label="Baseline call"
                recordings={recordings.baseline}
                id={`baseline-${rowData?.id}`}
              />
              <RecordingPlayer
                label="Simulated call"
                recordings={recordings.simulated}
                id={`simulated-${rowData?.id}`}
              />
            </Box>
          </Stack>
        )}

        <CompareTranscript
          data={transcripts}
          isLoading={isLoadingBaselineVsReplay}
        />
      </Stack>
    </Box>
  );
}

BaseLineVsReplay.propTypes = {
  rowData: PropTypes.object.isRequired,
  onBack: PropTypes.func.isRequired,
};
