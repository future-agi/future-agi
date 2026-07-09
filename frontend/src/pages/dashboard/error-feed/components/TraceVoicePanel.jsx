import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import VoiceLeftPanel from "src/components/VoiceDetailDrawerV2/VoiceLeftPanel";
import TraceVoiceSkeleton from "./TraceVoiceSkeleton";

function TraceVoicePanel({ traceId, projectId }) {
  const {
    data: voiceCallData,
    isFetching,
    error,
  } = useVoiceCallDetail(traceId, !!traceId);

  if (error) {
    return (
      <Typography
        typography="caption"
        color="error.main"
        sx={{ py: 2, textAlign: "center", display: "block" }}
      >
        Failed to load voice call
      </Typography>
    );
  }

  if (isFetching && !voiceCallData) {
    return <TraceVoiceSkeleton />;
  }

  if (!voiceCallData) {
    return (
      <Typography
        typography="caption"
        color="text.disabled"
        sx={{ py: 2, textAlign: "center", display: "block" }}
      >
        No voice call data available for this trace
      </Typography>
    );
  }

  const data = { ...voiceCallData, project_id: projectId, module: "project" };

  return (
    <Box
      sx={{
        height: "clamp(420px, 70vh, 700px)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <VoiceLeftPanel data={data} embedded />
    </Box>
  );
}

TraceVoicePanel.propTypes = {
  traceId: PropTypes.string,
  projectId: PropTypes.string,
};

export default TraceVoicePanel;
