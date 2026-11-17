import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import TranscriptView from "src/components/VoiceDetailDrawerV2/TranscriptView";
import VoiceAudioBridge from "src/components/VoiceDetailDrawerV2/VoiceAudioBridge";
import CallDetailsBar from "src/components/VoiceDetailDrawerV2/CallDetailsBar";
import { getSpanAttributes } from "src/components/traceDetailDrawer/DrawerRightRenderer/getSpanData";
import { findConversationSpan } from "./sharedViewHelpers";
import { normalizeRecordings } from "src/utils/utils";

function decodeJsonValue(value) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

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
  const rawLog = decodeJsonValue(attrs?.rawLog || attrs?.raw_log) || {};

  // Collector attributes may be JSON strings. Prefer the normalized transcript
  // over provider-native text, and do not let invalid/empty values hide it.
  const transcript =
    [
      resourceData.transcript,
      attrs["fi.conversation.transcript"],
      rawLog.transcript,
      attrs.transcript,
      conversationSpan?.transcript,
    ]
      .map(decodeJsonValue)
      .find((value) => Array.isArray(value) && value.length > 0) || null;

  const recordings =
    resourceData.recording ||
    resourceData.recordings ||
    rawLog.recording ||
    rawLog.recordings ||
    attrs.recording ||
    attrs.recordings ||
    null;

  const audioUrl =
    resourceData.audio_url ||
    resourceData.audioUrl ||
    rawLog.audio_url ||
    attrs.audio_url ||
    null;

  const legacyRecordings = normalizeRecordings(decodeJsonValue(recordings));
  // Project-mode audio consumes `recording`, not `recordings`/`audio_url`.
  // Use the accepted flat input shape (without the normalizer's `mono` alias).
  const recording = {
    stereo: attrs["conversation.recording.stereo"] || legacyRecordings.stereo,
    combined:
      attrs["conversation.recording.mono.combined"] ||
      legacyRecordings.combined ||
      audioUrl ||
      "",
    assistant:
      attrs["conversation.recording.mono.assistant"] ||
      legacyRecordings.assistant,
    customer:
      attrs["conversation.recording.mono.customer"] ||
      legacyRecordings.customer,
    stereo_channels: legacyRecordings.stereoChannels,
  };
  const recordingAvailable = Boolean(
    recording.stereo ||
      recording.combined ||
      recording.assistant ||
      recording.customer,
  );

  // Build a data object matching what VoiceDetailDrawerV2 expects
  return {
    // Identifier + module hint (drives voice branches in child components)
    id: resourceData.id || conversationSpan?.id,
    trace_id: resourceData.trace_id || resourceData.traceId,
    module: "project",
    // Voice fields
    transcript,
    recording,
    recording_available: recordingAvailable,
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
    data.module === "project"
      ? data.recording_available
      : data.audio_url ||
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
                textTransform: "none",
                letterSpacing: 0,
                mb: 0.75,
              }}
            >
              Recording
            </Typography>
            <VoiceAudioBridge data={data} />
          </Box>
        ) : (
          <Alert
            severity="info"
            icon={<Iconify icon="solar:volume-cross-linear" />}
          >
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
