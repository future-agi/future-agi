import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography, keyframes } from "@mui/material";
import Iconify from "src/components/iconify";
import TranscriptView from "src/components/VoiceDetailDrawerV2/TranscriptView";
import { getLoadingStateWithRespectiveStatus } from "src/sections/test-detail/common";
import { SKELETON_BUBBLE_WIDTHS, SYSTEM_SPEAKER_ROLE } from "./constants";
import {
  countWords,
  getChatTurnContent,
  getChatTurnTimestampMs,
} from "./chatTranscriptUtils";

/**
 * Chat transcript panel for the revamped chat drawer. Reuses the voice
 * drawer's `TranscriptView` for parity (search, role filters, per-turn
 * timestamps, keyboard nav); chat turns have no audio, so the audio-sync
 * highlight simply stays disabled.
 */

const pulse = keyframes`
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.65; }
`;

const SkeletonBubble = ({ align, delay = 0, width }) => (
  <Stack
    direction="row"
    sx={{
      width: "100%",
      justifyContent: align === "right" ? "flex-end" : "flex-start",
      animation: `${pulse} 1.6s ease-in-out infinite`,
      animationDelay: `${delay}ms`,
    }}
  >
    <Stack sx={{ maxWidth: "70%", minWidth: 140, gap: 0.5 }}>
      <Skeleton
        variant="rounded"
        animation="wave"
        height={44}
        width={width}
        sx={{
          borderRadius:
            align === "right" ? "12px 12px 4px 12px" : "12px 12px 12px 4px",
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? "rgba(255,255,255,0.06)"
              : "rgba(0,0,0,0.06)",
        }}
      />
      <Skeleton
        variant="text"
        animation="wave"
        width={56}
        sx={{
          alignSelf: align === "right" ? "flex-end" : "flex-start",
          fontSize: 10,
          opacity: 0.6,
        }}
      />
    </Stack>
  </Stack>
);

SkeletonBubble.propTypes = {
  align: PropTypes.oneOf(["left", "right"]).isRequired,
  delay: PropTypes.number,
  width: PropTypes.number,
};

const TranscriptLoading = () => (
  <Stack
    flex={1}
    spacing={1.5}
    sx={{ width: "100%", py: 2, pr: 1, overflow: "hidden" }}
  >
    {SKELETON_BUBBLE_WIDTHS.map((width, idx) => (
      <SkeletonBubble
        key={width}
        align={idx % 2 === 0 ? "left" : "right"}
        delay={idx * 120}
        width={width}
      />
    ))}
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="center"
      spacing={1}
      sx={{ pt: 1.5 }}
    >
      <Iconify
        icon="svg-spinners:3-dots-bounce"
        width={20}
        sx={{ color: "text.secondary" }}
      />
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ letterSpacing: "0.01em" }}
      >
        Loading transcript…
      </Typography>
    </Stack>
  </Stack>
);

const TranscriptEmpty = () => (
  <Stack
    flex={1}
    alignItems="center"
    justifyContent="center"
    spacing={1}
    sx={{ width: "100%", py: 6, color: "text.secondary" }}
  >
    <Iconify icon="mdi:message-text-outline" width={28} />
    <Typography typography="s2" color="text.secondary">
      No messages in this chat
    </Typography>
  </Stack>
);

const ChatTranscriptView = ({ data }) => {
  const filteredTranscript = useMemo(() => {
    const transcript = data?.transcript;
    if (!Array.isArray(transcript)) return [];
    return transcript
      .filter((item) => item.speaker_role !== SYSTEM_SPEAKER_ROLE)
      .map((item) => {
        const ts = getChatTurnTimestampMs(item);
        const content = getChatTurnContent(item);
        const existingDuration =
          typeof item.duration === "number" && Number.isFinite(item.duration)
            ? item.duration
            : null;
        return {
          ...item,
          // Flatten `messages[0].content` into a plain `content` field so
          // TranscriptView's enrichTurns/getContent picks it up.
          content,
          ...(ts != null ? { startTimeSeconds: ts } : {}),
          ...(existingDuration == null
            ? { duration: countWords(content) }
            : {}),
        };
      });
  }, [data?.transcript]);

  const { isCallInProgress } = getLoadingStateWithRespectiveStatus(
    data?.status,
    data?.simulation_call_type,
  );

  if (filteredTranscript.length === 0) {
    // Still running → loading; finished with no turns → real empty state.
    return isCallInProgress ? <TranscriptLoading /> : <TranscriptEmpty />;
  }

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
      <TranscriptView
        transcript={filteredTranscript}
        // Repurpose TranscriptView's TalkRatioBar as a compact speaker legend.
        hideTimelineStrip
        hideTalkRatioLabel
        hideTalkRatioPercentages
        talkRatioLegendAlign="left"
        hideSilenceMarkers
      />
    </Box>
  );
};

ChatTranscriptView.propTypes = {
  data: PropTypes.object,
};

export default ChatTranscriptView;
