import { Box, Grid, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { lazy, Suspense } from "react";
import {
  CompareConversationSkeleton,
  PerformanceMetricsSkeleton,
  TestDetailDrawerScenarioTableSkeleton,
} from "./Skeletons";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { StereoMultiTrackPlayer } from "../AudioPlayerCustom";
const PerformanceMetrics = lazy(() => import("./PerformanceMetrics"));
const CompareConversation = lazy(() => import("./CompareConversation"));
const TestDetailDrawerScenarioTable = lazy(
  () =>
    import(
      "src/sections/test-detail/TestDetailDrawer/TestDetailDrawerScenarioTable"
    ),
);
import { transformToConversations } from "./common";

const Header = ({ scenarioName, sessionId }) => {
  return (
    <Stack gap={0}>
      <Typography
        typography={"m3"}
        fontWeight={"fontWeightSemiBold"}
        color={"text.primary"}
      >
        Base Line vs Replay
      </Typography>
      <Typography
        typography={"s2_1"}
        fontWeight={"fontWeightRegular"}
        color={"text.disabled"}
      >
        {scenarioName}
        {sessionId ? ` | Session ID: ${sessionId}` : ""}
      </Typography>
    </Stack>
  );
};
Header.propTypes = {
  scenarioName: PropTypes.string,
  sessionId: PropTypes.string,
};

/**
 * Maps API recording format to the shape StereoMultiTrackPlayer expects.
 */
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
  if (!hasAudio) {
    return (
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          p: 2,
        }}
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
          sx={{ mb: 1 }}
        >
          {label}
        </Typography>
        <Typography typography="s2_1" color="text.disabled">
          No recording available
        </Typography>
      </Box>
    );
  }
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        p: 2,
      }}
    >
      <Typography
        typography="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
        sx={{ mb: 1 }}
      >
        {label}
      </Typography>
      <StereoMultiTrackPlayer recordings={mapped} id={id} height={50} />
    </Box>
  );
};

RecordingPlayer.propTypes = {
  label: PropTypes.string.isRequired,
  recordings: PropTypes.object,
  id: PropTypes.string,
};

export default function BaseLineVsReplay({ rowData }) {
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

  return (
    <Box
      sx={{
        minHeight: "80vh",
        display: "flex",
        flexDirection: "column",
        px: 2,
        gap: 2,
      }}
    >
      <Header
        scenarioName={rowData?.scenario}
        sessionId={rowData?.session_id ?? rowData?.sessionId}
      />
      <Suspense fallback={<PerformanceMetricsSkeleton />}>
        <PerformanceMetrics
          data={baselineVsReplayData?.comparison_metrics}
          isLoading={isLoadingBaselineVsReplay}
          simulationCallType={rowData?.simulation_call_type}
        />
      </Suspense>
      <Suspense fallback={<TestDetailDrawerScenarioTableSkeleton />}>
        <Box
          sx={{
            "& > div": {
              marginX: 0,
            },
          }}
        >
          <TestDetailDrawerScenarioTable data={rowData} />
        </Box>
      </Suspense>
      {baselineVsReplayData?.comparisonRecordings && (
        <Stack gap={1.5}>
          <Typography
            typography="m3"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            Call Recordings
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12} md={6}>
              <RecordingPlayer
                label="Baseline Call"
                recordings={baselineVsReplayData.comparisonRecordings.baseline}
                id={`baseline-${rowData?.id}`}
              />
            </Grid>
            <Grid item xs={12} md={6}>
              <RecordingPlayer
                label="Simulated Call"
                recordings={baselineVsReplayData.comparisonRecordings.simulated}
                id={`simulated-${rowData?.id}`}
              />
            </Grid>
          </Grid>
        </Stack>
      )}
      <Suspense fallback={<CompareConversationSkeleton />}>
        <CompareConversation
          data={
            isLoadingBaselineVsReplay
              ? null
              : transformToConversations(
                  baselineVsReplayData?.comparisonTranscripts,
                )
          }
          isLoading={isLoadingBaselineVsReplay}
          simulationCallType={rowData?.simulation_call_type}
        />
      </Suspense>
    </Box>
  );
}

BaseLineVsReplay.propTypes = {
  rowData: PropTypes.object.isRequired,
};
