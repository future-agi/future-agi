import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, CircularProgress, Typography } from "@mui/material";
import LoadingButton from "@mui/lab/LoadingButton";
import Button from "@mui/material/Button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import { canonicalEntries } from "src/utils/utils";

/**
 * Unified error-localization section for an eval row. Supports two
 * backends and picks between them based on which IDs the caller supplies:
 *
 *   1. Cell mode — caller passes `cellId`. Matches the dataset drawer's
 *      ErrorLocalizationCellSection: POST+GET
 *      /model-hub/cells/:cellId/run-error-localizer/.
 *
 *   2. Trace mode — caller passes `observationSpanId` and
 *      `customEvalConfigId`. Fetches error analysis via
 *      /tracer/observation-span/get_evaluation_details (the same endpoint
 *      the existing view-details modal uses). Since the trace backend
 *      doesn't expose a standalone "run localization" trigger, the Run
 *      button in trace mode re-runs the eval via
 *      /tracer/custom-eval-config/run_evaluation/ (which recomputes the
 *      error analysis as a side effect).
 *
 * UI states across both modes:
 *   • completed + analysis → ErrorLocalizeCard with highlighted segments
 *   • running / pending    → purple spinner banner
 *   • failed               → red banner + Retry button
 *   • skipped              → italic caption
 *   • not yet run          → dashed card + Run button
 */
const EvalErrorLocalization = ({
  cellId,
  observationSpanId,
  customEvalConfigId,
  projectVersionId,
  initialAnalysis,
  initialStatus,
  datapoint,
  selectedInputKey,
}) => {
  const queryClient = useQueryClient();
  const mode = cellId
    ? "cell"
    : observationSpanId && customEvalConfigId
      ? "trace"
      : null;

  const [requested, setRequested] = useState(false);
  const [overrideAnalysis, setOverrideAnalysis] = useState(null);

  // ── Cell-mode polling ───────────────────────────────────────────────────
  const cellPollEnabled =
    mode === "cell" && !initialAnalysis && !overrideAnalysis;
  const { data: cellPollData } = useQuery({
    queryKey: ["eval-error-localizer", cellId, requested],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.develop.eval.getCellErrorLocalizer(cellId),
      );
      return data?.result || null;
    },
    enabled: cellPollEnabled,
    refetchInterval: (q) => {
      const r = q?.state?.data;
      if (!r) return false;
      const status = r.status;
      if (status === "pending" || status === "running") return 3000;
      return false;
    },
    refetchOnWindowFocus: false,
  });

  // ── Trace-mode lazy fetch ───────────────────────────────────────────────
  const traceFetchEnabled = mode === "trace";
  const { data: traceData, isLoading: traceLoading } = useQuery({
    queryKey: ["eval-details", observationSpanId, customEvalConfigId],
    queryFn: async () => {
      const { data } = await axios.get(
        endpoints.project.getEvalDetails(observationSpanId, customEvalConfigId),
      );
      return data?.result || null;
    },
    enabled: traceFetchEnabled,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (!cellPollData) return;
    if (cellPollData.status === "completed" && cellPollData.error_analysis) {
      setOverrideAnalysis(cellPollData.error_analysis);
    }
    if (
      !requested &&
      (cellPollData.status === "pending" || cellPollData.status === "running")
    ) {
      setRequested(true);
    }
  }, [cellPollData, requested]);

  // Pick best analysis across both modes.
  const analysis =
    overrideAnalysis ||
    initialAnalysis ||
    traceData?.errorAnalysis ||
    traceData?.error_analysis ||
    null;

  // ── Cell-mode trigger ───────────────────────────────────────────────────
  const cellTriggerMutation = useMutation({
    mutationFn: async () => {
      const { data } = await axios.post(
        endpoints.develop.eval.runCellErrorLocalizer(cellId),
        {},
      );
      return data?.result;
    },
    onSuccess: () => {
      setRequested(true);
      queryClient.invalidateQueries({
        queryKey: ["eval-error-localizer", cellId],
      });
    },
  });

  // ── Trace-mode trigger (re-runs the eval config, which recomputes
  // error analysis on the next tick). ────────────────────────────────────
  const traceTriggerMutation = useMutation({
    mutationFn: async () => {
      if (!projectVersionId) {
        throw new Error("project_version_id not available for this eval");
      }
      const { data } = await axios.post(
        endpoints.project.reRunTracerEvalutation,
        {
          project_version_id: projectVersionId,
          custom_eval_config_id: customEvalConfigId,
        },
      );
      return data?.result;
    },
    onSuccess: () => {
      enqueueSnackbar(
        "Re-running evaluation — localization will appear when it finishes.",
        { variant: "info" },
      );
      // Invalidate so that when the user reopens the row the fresh results
      // get picked up.
      queryClient.invalidateQueries({
        queryKey: ["eval-details", observationSpanId, customEvalConfigId],
      });
    },
    onError: (err) => {
      enqueueSnackbar(
        err?.response?.data?.result ||
          err?.message ||
          "Failed to start error localization.",
        { variant: "error" },
      );
    },
  });

  // Dispatch the right trigger for the active mode.
  const triggerMutation =
    mode === "cell" ? cellTriggerMutation : traceTriggerMutation;

  const effectiveStatus = analysis
    ? "completed"
    : cellPollData?.status || initialStatus || null;

  // ── Loading state (trace mode, waiting on first fetch) ─────────────────
  if (mode === "trace" && traceLoading && !analysis) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.25,
          py: 1,
          color: "text.disabled",
        }}
      >
        <CircularProgress size={12} thickness={5} />
        <Typography variant="caption" sx={{ fontSize: 11 }}>
          Checking error localization…
        </Typography>
      </Box>
    );
  }

  // ── State 1: completed + analysis ────────────────────────────────────────
  if (analysis) {
    const entries =
      analysis && typeof analysis === "object" && !Array.isArray(analysis)
        ? canonicalEntries(analysis).filter(
            ([, v]) => Array.isArray(v) && v.length > 0,
          )
        : null;

    if (!entries || entries.length === 0) {
      // Empty object — analysis ran but found no segments. Fall through
      // to the "run" card so users can re-trigger if they suspect it.
    } else {
      return (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <Typography
            variant="caption"
            sx={{
              fontSize: 10,
              fontWeight: 600,
              color: "text.secondary",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
            }}
          >
            Possible Error
          </Typography>
          {entries.map(([key, value]) => (
            <ErrorLocalizeCard
              key={key}
              value={value}
              column={selectedInputKey || key}
              tabValue="raw"
              datapoint={datapoint}
            />
          ))}
        </Box>
      );
    }
  }

  // Without any IDs we can't offer run/retry actions.
  if (!mode) return null;

  // ── State 2: running ─────────────────────────────────────────────────────
  if (effectiveStatus === "pending" || effectiveStatus === "running") {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.25,
          px: 1.25,
          py: 1,
          borderRadius: "6px",
          border: "1px solid",
          borderColor: "primary.main",
          backgroundColor: (theme) =>
            theme.palette.mode === "dark"
              ? "rgba(124, 77, 255, 0.08)"
              : "rgba(124, 77, 255, 0.04)",
        }}
      >
        <CircularProgress size={13} thickness={5} />
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
            sx={{ display: "block", fontSize: 10 }}
          >
            Usually 30–90 seconds.
          </Typography>
        </Box>
      </Box>
    );
  }

  // ── State 3: failed ──────────────────────────────────────────────────────
  if (effectiveStatus === "failed") {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 0.75,
          px: 1.25,
          py: 1,
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
        {cellPollData?.error_message && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontSize: 10 }}
          >
            {cellPollData.error_message}
          </Typography>
        )}
        <Box>
          <Button
            size="small"
            variant="outlined"
            color="primary"
            onClick={() => triggerMutation.mutate()}
            disabled={triggerMutation.isPending}
            sx={{ textTransform: "none", fontSize: 11, mt: 0.25 }}
          >
            Retry
          </Button>
        </Box>
      </Box>
    );
  }

  // ── State 4: skipped ─────────────────────────────────────────────────────
  if (effectiveStatus === "skipped") {
    return (
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontSize: 10, fontStyle: "italic" }}
      >
        Error localization was skipped — input data isn&apos;t available to
        localize on.
      </Typography>
    );
  }

  // ── State 5: not yet run — show dashed card with Run button ──────────────
  const canRun =
    mode === "cell" ? !!cellId : !!(customEvalConfigId && projectVersionId);

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1.25,
        px: 1.25,
        py: 1,
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
        width={16}
        sx={{ color: "primary.main", flexShrink: 0 }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          variant="caption"
          fontWeight={600}
          sx={{ display: "block", fontSize: 11 }}
        >
          No error localization yet
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", fontSize: 10 }}
        >
          {mode === "cell"
            ? "Pinpoint which parts of the input caused this eval to fail."
            : canRun
              ? "Re-run the evaluation to compute which parts of the input failed."
              : "Localization is not available for this eval."}
        </Typography>
      </Box>
      {canRun && (
        <LoadingButton
          size="small"
          variant="contained"
          color="primary"
          loading={triggerMutation.isPending}
          onClick={() => triggerMutation.mutate()}
          sx={{
            textTransform: "none",
            fontSize: 11,
            flexShrink: 0,
            height: 26,
          }}
        >
          Run
        </LoadingButton>
      )}
    </Box>
  );
};

EvalErrorLocalization.propTypes = {
  cellId: PropTypes.string,
  observationSpanId: PropTypes.string,
  customEvalConfigId: PropTypes.string,
  projectVersionId: PropTypes.string,
  initialAnalysis: PropTypes.object,
  initialStatus: PropTypes.string,
  datapoint: PropTypes.object,
  selectedInputKey: PropTypes.string,
};

export default EvalErrorLocalization;
