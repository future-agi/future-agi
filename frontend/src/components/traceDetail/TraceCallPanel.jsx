import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import VoiceLeftPanel from "src/components/VoiceDetailDrawerV2/VoiceLeftPanel";
import ContentSkeleton from "src/components/content-skeleton";

const Message = ({ children, color = "text.disabled" }) => (
  <Typography
    typography="caption"
    color={color}
    sx={{ py: 2, textAlign: "center", display: "block" }}
  >
    {children}
  </Typography>
);

Message.propTypes = { children: PropTypes.node, color: PropTypes.string };

/**
 * The trace drawer's Conversation view: the Voice Observability left panel
 * (recording on top, then the transcript) for a call recorded as an
 * attachment. It reads the same call detail Voice Observability reads.
 */
export default function TraceCallPanel({ traceId, projectId }) {
  const { data, isFetching, error } = useVoiceCallDetail(traceId, {
    enabled: !!traceId,
    projectId,
  });

  const call = useMemo(
    () =>
      data && {
        // This call was found through its attachment recording: never fall
        // back to legacy recording URLs when the projection is absent.
        recording_attachment: null,
        ...data,
        id: data.id || traceId,
        trace_id: data.trace_id || traceId,
        project_id: projectId,
        module: "project",
      },
    [data, traceId, projectId],
  );

  if (error) return <Message color="error.main">Failed to load call</Message>;
  if (isFetching && !call) return <ContentSkeleton variant="panel" />;
  if (!call) return <Message>No call data available for this trace</Message>;

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
      {/* Round play and the peaks waveform (seek bar without peaks). */}
      <VoiceLeftPanel data={call} recordingVariant="trace" />
    </Box>
  );
}

TraceCallPanel.propTypes = {
  traceId: PropTypes.string,
  projectId: PropTypes.string,
};
