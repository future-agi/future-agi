import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import VoiceLeftPanel from "src/components/VoiceDetailDrawerV2/VoiceLeftPanel";
import ContentSkeleton from "src/components/content-skeleton";
import { buildCallActivity, withTurnIds } from "./callActivity";

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
 * attachment. It reads the same call detail Voice Observability reads, and
 * adds the call's tool spans from the drawer's tree as background activity;
 * selecting one opens that span on the right.
 */
export default function TraceCallPanel({
  traceId,
  projectId,
  spans,
  onSelectSpan,
}) {
  const { data, isFetching, error } = useVoiceCallDetail(traceId, {
    enabled: !!traceId,
    projectId,
  });

  // The root this call detail chose; activity comes only from its subtree.
  const rootSpanId = data?.observation_span?.[0]?.id ?? null;
  // Ids follow the call detail alone, so a tree refresh never hands the
  // transcript a new identity (which would move its scroll).
  const transcript = useMemo(
    () => withTurnIds(data?.transcript),
    [data?.transcript],
  );
  const activity = useMemo(
    () => buildCallActivity({ tree: spans, rootSpanId, transcript }).activity,
    [spans, rootSpanId, transcript],
  );

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
        ...(Array.isArray(data.transcript) && { transcript }),
      },
    [data, traceId, projectId, transcript],
  );

  if (error) return <Message color="error.main">Failed to load call</Message>;
  if (isFetching && !call) return <ContentSkeleton variant="panel" />;
  if (!call) return <Message>No call data available for this trace</Message>;

  return (
    <Box
      sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
    >
      {/* Round play and the peaks waveform (seek bar without peaks). */}
      <VoiceLeftPanel
        data={call}
        recordingVariant="trace"
        activity={activity}
        onSelectActivity={onSelectSpan}
      />
    </Box>
  );
}

TraceCallPanel.propTypes = {
  traceId: PropTypes.string,
  projectId: PropTypes.string,
  // The drawer's unfiltered span tree and its non-toggling span selection.
  spans: PropTypes.array,
  onSelectSpan: PropTypes.func,
};
