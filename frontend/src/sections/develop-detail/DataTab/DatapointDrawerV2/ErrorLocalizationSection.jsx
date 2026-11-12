import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, CircularProgress, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import AudioErrorCard from "src/components/custom-audio/AudioErrorCard";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import SkippedLocalizationBanner from "src/sections/common/SkippedLocalizationBanner";
import axios, { endpoints } from "src/utils/axios";
import { canonicalEntries } from "src/utils/utils";

// "Possible error" for a failed eval cell: inline error analysis, or run /
// poll the cell error localizer. Moved unchanged from the old 550 px row
// drawer (DatapointDrawerV2.jsx) into the Row view's eval chip detail.
const ErrorLocalizationSection = ({ evalOpen, onAnalysisLoaded }) => {
  const queryClient = useQueryClient();
  const cellId = evalOpen?.cell_id || evalOpen?.cellId || evalOpen?.id;
  // Keep a stable ref so the polling effect never needs the callback in its
  // deps — avoids an infinite loop when the parent passes an inline arrow.
  const onAnalysisLoadedRef = useRef(onAnalysisLoaded);
  useEffect(() => {
    onAnalysisLoadedRef.current = onAnalysisLoaded;
  }, [onAnalysisLoaded]);
  const valueInfos = evalOpen?.value_infos;
  const inlineAnalysis = valueInfos?.error_analysis;
  const hasInlineAnalysis = !!(
    inlineAnalysis &&
    typeof inlineAnalysis === "object" &&
    Object.values(inlineAnalysis).some((v) => Array.isArray(v) && v.length > 0)
  );

  const [requested, setRequested] = useState(false);
  const [activeTaskId, setActiveTaskId] = useState(null);

  const { data: pollData } = useQuery({
    queryKey: ["cell-error-localizer", cellId, requested],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getCellErrorLocalizer(cellId),
      );
      return data?.result || null;
    },
    enabled: !!cellId && !hasInlineAnalysis,
    refetchInterval: (q) => {
      const r = q?.state?.data;
      if (!r) return false;
      const status = r.status;
      // After re-run, backend can return the previous completed task once.
      // Keep polling until the newly started task_id appears.
      if (activeTaskId && r.task_id !== activeTaskId) return 3000;
      // Keep polling while pending/running.
      if (status === "pending" || status === "running") return 3000;
      return false;
    },
    refetchOnWindowFocus: false,
  });

  // When the task transitions to completed, push the analysis upward
  // and stop the poll.
  useEffect(() => {
    if (!pollData) return;
    if (pollData.status === "completed" && pollData.error_analysis) {
      onAnalysisLoadedRef.current?.({
        error_analysis: pollData.error_analysis,
        selected_input_key: pollData.selected_input_key,
        input_data: pollData.input_data,
        input_types: pollData.input_types,
      });
    }
    // If we found an in-flight task on mount (without the user having
    // clicked the button), surface the running banner by flipping
    // requested.
    if (
      !requested &&
      (pollData.status === "pending" || pollData.status === "running")
    ) {
      setRequested(true);
    }
  }, [pollData, requested]);

  // Trigger mutation
  const triggerMutation = useMutation({
    mutationFn: async () => {
      const { data } = await axios.post(
        endpoints.develop.eval.runCellErrorLocalizer(cellId),
        {},
      );
      return data?.result;
    },
    onSuccess: (res) => {
      setActiveTaskId(res?.task_id || null);
      setRequested(true);
      // Invalidate the poll query so it kicks off immediately.
      queryClient.invalidateQueries({
        queryKey: ["cell-error-localizer", cellId],
      });
    },
    onError: () => {
      setRequested(false);
      setActiveTaskId(null);
    },
  });

  const renderAnalysis =
    inlineAnalysis ||
    (pollData?.status === "completed" ? pollData?.error_analysis : null);
  const renderAnalysisEntries = canonicalEntries(renderAnalysis || {}).filter(
    ([_, value]) => Array.isArray(value) && value.length > 0,
  );
  const hasRenderAnalysis = renderAnalysisEntries.length > 0;
  const renderAnalysisForDisplay = Object.fromEntries(renderAnalysisEntries);
  const renderSelectedInputKey =
    valueInfos?.selected_input_key || pollData?.selected_input_key;
  const renderValueInfos = {
    ...(valueInfos || {}),
    errorAnalysis: renderAnalysisForDisplay,
    selected_input_key: renderSelectedInputKey,
    input_data: valueInfos?.input_data || pollData?.input_data,
    input_types: valueInfos?.input_types || pollData?.input_types,
  };

  // Branch 1: existing inline analysis — render via the original path.
  if (hasRenderAnalysis) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          height: "100%",
          marginTop: 1,
          overflowWrap: "break-word",
          position: "relative",
        }}
      >
        <Typography
          fontWeight="fontWeightMedium"
          variant="s1"
          color="text.primary"
        >
          Possible error
        </Typography>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            overflowY: "auto",
          }}
        >
          {(() => {
            const hasOrgSegment = renderAnalysisEntries
              .flatMap(([_, value]) => value)
              .some((entry) => entry?.orgSegment);
            if (hasOrgSegment) {
              return (
                <AudioErrorCard
                  valueInfos={renderValueInfos}
                  column={renderSelectedInputKey}
                />
              );
            }
            return renderAnalysisEntries.map(([key, value]) => (
              <ErrorLocalizeCard
                key={key}
                value={value}
                column={renderSelectedInputKey}
                tabValue="raw"
                datapoint={renderValueInfos}
              />
            ));
          })()}
        </Box>
      </Box>
    );
  }

  if (!cellId) return null;

  const status = pollData?.status;
  const waitingForActiveTask =
    !!activeTaskId && pollData?.task_id !== activeTaskId;
  const isRunning =
    status === "pending" || status === "running" || waitingForActiveTask;
  const isFailed = status === "failed";
  const isSkipped = status === "skipped";
  const isCompleted = status === "completed";

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        marginTop: 1,
      }}
    >
      <Typography
        fontWeight="fontWeightMedium"
        variant="s1"
        color="text.primary"
      >
        Possible error
      </Typography>
      {isRunning ? (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1.5,
            px: 1.5,
            py: 1.25,
            borderRadius: "6px",
            border: "1px solid",
            borderColor: "primary.main",
            backgroundColor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(124, 77, 255, 0.08)"
                : "rgba(124, 77, 255, 0.04)",
          }}
        >
          <CircularProgress size={14} thickness={5} />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              variant="caption"
              fontWeight={600}
              sx={{ display: "block", color: "primary.main" }}
            >
              Error localization running…
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", fontSize: "11px" }}
            >
              Pinpointing which parts of the input caused the failure. Usually
              30–90 seconds.
            </Typography>
          </Box>
        </Box>
      ) : isFailed ? (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1,
            px: 1.5,
            py: 1.25,
            borderRadius: "6px",
            border: "1px solid",
            borderColor: "error.light",
            backgroundColor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(255, 86, 48, 0.08)"
                : "rgba(255, 86, 48, 0.04)",
          }}
        >
          <Typography variant="caption" fontWeight={600} color="error.main">
            Error localization failed
          </Typography>
          {pollData?.error_message && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "11px" }}
            >
              {pollData.error_message}
            </Typography>
          )}
          <Box>
            <Button
              size="small"
              variant="outlined"
              color="primary"
              onClick={() => triggerMutation.mutate()}
              disabled={triggerMutation.isPending}
              sx={{ textTransform: "none", fontSize: "12px", mt: 0.5 }}
            >
              Retry
            </Button>
          </Box>
        </Box>
      ) : isSkipped ? (
        <SkippedLocalizationBanner message={pollData?.error_message} />
      ) : (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1.5,
            px: 1.5,
            py: 1.25,
            borderRadius: "6px",
            border: "1px dashed",
            borderColor: "divider",
            backgroundColor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(255,255,255,0.02)"
                : "rgba(0,0,0,0.02)",
          }}
        >
          <Iconify
            icon="solar:target-bold"
            width={18}
            sx={{ color: "primary.main", flexShrink: 0 }}
          />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              variant="caption"
              fontWeight={600}
              sx={{ display: "block" }}
            >
              No error localization yet
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", fontSize: "11px" }}
            >
              Pinpoint which parts of the input caused this eval to fail.
            </Typography>
          </Box>
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            loading={triggerMutation.isPending}
            onClick={() => triggerMutation.mutate()}
            sx={{
              textTransform: "none",
              fontSize: "12px",
              flexShrink: 0,
              height: 30,
            }}
          >
            Run
          </LoadingButton>
        </Box>
      )}
      {isCompleted && (
        <Typography variant="caption" color="text.secondary">
          Error localization completed — no error segments were found for this
          input.
        </Typography>
      )}
      {triggerMutation.isError && (
        <Typography
          variant="caption"
          color="error.main"
          sx={{ fontSize: "11px" }}
        >
          {triggerMutation.error?.response?.data?.result ||
            triggerMutation.error?.message ||
            "Failed to start error localization."}
        </Typography>
      )}
    </Box>
  );
};

ErrorLocalizationSection.propTypes = {
  evalOpen: PropTypes.object,
  onAnalysisLoaded: PropTypes.func,
};

export default ErrorLocalizationSection;
