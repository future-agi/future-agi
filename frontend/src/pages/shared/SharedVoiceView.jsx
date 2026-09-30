import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import TranscriptView from "src/components/VoiceDetailDrawerV2/TranscriptView";
import VoiceAudioBridge from "src/components/VoiceDetailDrawerV2/VoiceAudioBridge";
import CallDetailsBar from "src/components/VoiceDetailDrawerV2/CallDetailsBar";
import { getSpanAttributes } from "src/components/traceDetailDrawer/DrawerRightRenderer/getSpanData";
import { findConversationSpan } from "./sharedViewHelpers";

/**
 * Extract voice-shaped data from the shared trace payload. The backend may
 * surface voice fields in several places; we try each in order.
 */
function extractVoiceData(resourceData) {
  if (!resourceData) return null;

  const spans =
    resourceData.observationSpans ||
    resourceData.observation_spans ||
    resourceData.spans ||
    [];
  const found = findConversationSpan(spans);
  const conversationSpan = found?.span;

  const attrs = getSpanAttributes(conversationSpan);
  const rawLog = attrs?.rawLog || attrs?.raw_log || {};

  // Transcript — try top-level, then conversation span's rawLog, then attrs
  const transcript =
    resourceData.transcript ||
    rawLog.transcript ||
    attrs.transcript ||
    conversationSpan?.transcript ||
    null;

  // Recordings — support multiple shapes
  const recordings =
    resourceData.recordings || rawLog.recordings || attrs.recordings || null;

  const audioUrl =
    resourceData.audio_url ||
    resourceData.audioUrl ||
    rawLog.audio_url ||
    attrs.audio_url ||
    null;

  // Build a data object matching what VoiceDetailDrawerV2 expects
  return {
    // Identifier + module hint (drives voice branches in child components)
    id: resourceData.id || conversationSpan?.id,
    trace_id: resourceData.trace_id || resourceData.traceId,
    module: "project",
    // Voice fields
    transcript,
    recordings,
    audio_url: audioUrl,
    audioUrl,
    // Metadata for CallDetailsBar
    call_type: resourceData.call_type || resourceData.callType,
    status: resourceData.status || conversationSpan?.status,
    duration_seconds:
      resourceData.duration_seconds ??
      resourceData.durationSeconds ??
      rawLog.duration_seconds ??
      null,
    phone_number: resourceData.phone_number || rawLog.phone_number,
    ended_reason: resourceData.ended_reason || rawLog.ended_reason,
    provider: resourceData.provider || rawLog.provider,
    timestamp: resourceData.timestamp || conversationSpan?.start_time,
    // For observation-span references inside shared components
    observation_span: conversationSpan ? [conversationSpan] : [],
  };
}

/**
 * Read-only voice call view rendered by SharedView. Reuses the same
 * TranscriptView / VoiceAudioBridge / CallDetailsBar components as the
 * authenticated voice drawer so shared links feel consistent. `voiceData`
 * skips trace extraction for callers that already hold drawer-shaped data.
 */
const SharedVoiceView = ({ resourceData, voiceData }) => {
  const data = useMemo(
    () => voiceData || extractVoiceData(resourceData),
    [voiceData, resourceData],
  );

  if (!data) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="warning">
          This shared voice call has no playable data.
        </Alert>
      </Box>
    );
  }

  const hasAudio =
    data.audio_url ||
    data.audioUrl ||
    data.recordings?.stereo ||
    data.recordings?.assistant ||
    data.recordings?.customer ||
    data.recordings?.combined;

  const hasTranscript =
    Array.isArray(data.transcript) && data.transcript.length > 0;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
        overflow: "hidden",
      }}
    >
      {/* Metadata row matching the authenticated voice drawer */}
      <CallDetailsBar data={data} />

      {/* Body: recording on top, transcript below */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          gap: 1,
          p: 1.5,
          overflow: "auto",
        }}
      >
        {hasAudio ? (
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              p: 1.25,
              bgcolor: "background.neutral",
              flexShrink: 0,
            }}
          >
            <Typography
              sx={{
                fontSize: 11,
                fontWeight: 600,
                color: "text.secondary",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                mb: 0.75,
              }}
            >
              Recording
            </Typography>
            <VoiceAudioBridge data={data} />
          </Box>
        ) : (
          <Alert severity="info" icon={<Iconify icon="mdi:volume-off" />}>
            Audio recording is not available in this shared view.
          </Alert>
        )}

        {hasTranscript ? (
          <Box
            sx={{
              flex: 1,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <TranscriptView transcript={data.transcript} />
          </Box>
        ) : (
          <Alert severity="info">
            Transcript is not available in this shared view.
          </Alert>
        )}
      </Box>
    </Box>
  );
};

SharedVoiceView.propTypes = {
  resourceData: PropTypes.object,
  voiceData: PropTypes.object,
};

export default SharedVoiceView;
