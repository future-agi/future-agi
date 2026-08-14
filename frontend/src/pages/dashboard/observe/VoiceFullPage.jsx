import React, { useCallback, useMemo } from "react";
import { useParams, useNavigate } from "react-router";
import { Helmet } from "react-helmet-async";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2";
import {
  useCallExecutionDetail,
  useVoiceCallDetail,
} from "src/sections/agents/helper";

// Full-page voice view. The URL id can be either:
//   • an observe Trace id — fetched via `voice_call_detail` and rendered
//     with `module: "project"` (annotations, analytics from the trace).
//   • a simulate CallExecution id — fetched via `/simulate/call-executions/`
//     and rendered with `module: "simulate"`.
//
// We attempt the observe fetch first, and fall back to the simulate fetch
// only when the first resolves empty or errors. This keeps share links
// working for simulate-only calls that have no tracing linked.
export default function VoiceFullPage() {
  const { observeId, callId } = useParams();
  const navigate = useNavigate();

  const {
    data: voiceDetail,
    isLoading: isLoadingVoice,
    isError: isVoiceError,
  } = useVoiceCallDetail(callId, true);

  // Enable the simulate fallback only after the observe fetch has
  // resolved without data. `enabled` flips asynchronously, so React Query
  // won't double-fire both requests for observe-owned ids.
  const shouldFallback =
    !!callId && !isLoadingVoice && (isVoiceError || !voiceDetail);

  const {
    data: callExecDetail,
    isLoading: isLoadingCallExec,
    isError: isCallExecError,
  } = useCallExecutionDetail(callId, shouldFallback);

  const isLoading = isLoadingVoice || (shouldFallback && isLoadingCallExec);

  const mergedData = useMemo(() => {
    if (voiceDetail) {
      return {
        ...voiceDetail,
        id: voiceDetail?.id || callId,
        trace_id: voiceDetail?.trace_id || callId,
        project_id: observeId,
        module: "project",
      };
    }
    if (callExecDetail) {
      return {
        ...callExecDetail,
        id: callExecDetail?.id || callId,
        project_id: observeId || callExecDetail?.project_id,
        module: "simulate",
      };
    }
    return null;
  }, [voiceDetail, callExecDetail, callId, observeId]);

  const handleClose = useCallback(() => {
    if (window.history.length > 1) {
      navigate(-1);
    } else if (observeId) {
      navigate(`/dashboard/observe/${observeId}/voice`);
    } else {
      window.close();
    }
  }, [navigate, observeId]);

  // Both fetches resolved (or the fallback wasn't enabled because the
  // first succeeded), but we still have no data — render a terminal
  // "not found" state instead of an infinite spinner.
  const resolved = !isLoadingVoice && (!shouldFallback || !isLoadingCallExec);
  const notFound =
    resolved &&
    !voiceDetail &&
    !callExecDetail &&
    (isVoiceError || isCallExecError || shouldFallback);

  return (
    <>
      <Helmet>
        <title>Voice call — {callId?.substring(0, 8) || "..."}</title>
      </Helmet>
      {isLoading ? (
        <Stack
          alignItems="center"
          justifyContent="center"
          sx={{ height: "100vh" }}
          gap={1.5}
        >
          <CircularProgress size={28} />
          <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
            Loading voice call…
          </Typography>
        </Stack>
      ) : notFound ? (
        <Stack
          alignItems="center"
          justifyContent="center"
          sx={{ height: "100vh", px: 2, textAlign: "center" }}
          gap={1.5}
        >
          <Typography sx={{ fontSize: 14, fontWeight: 600 }}>
            We couldn&apos;t load this voice call.
          </Typography>
          <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
            The call may not exist or belong to a different project.
          </Typography>
          <Button size="small" variant="outlined" onClick={handleClose}>
            Go back
          </Button>
        </Stack>
      ) : mergedData ? (
        <Box sx={{ height: "100vh", width: "100vw", overflow: "hidden" }}>
          <VoiceDetailDrawerV2
            data={mergedData}
            onClose={handleClose}
            hasPrev={false}
            hasNext={false}
            initialFullscreen
          />
        </Box>
      ) : null}
    </>
  );
}
