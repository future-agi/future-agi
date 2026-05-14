import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography, keyframes } from "@mui/material";
import Iconify from "src/components/iconify";
import TranscriptView from "src/components/VoiceDetailDrawerV2/TranscriptView";

/**
 * Chat transcript panel for the revamped chat drawer.
 *
 * Reuses the voice drawer's `TranscriptView` to get feature parity with
 * voice — search box, All/Assistant/Customer filters, per-turn timestamps,
 * interrupt/silence markers, keyboard navigation. Because chat transcripts
 * don't have audio, the `useVoiceAudioStore` reads inside `TranscriptView`
 * resolve to nullish `currentTime`/`duration`, which keeps `playingIdx` at
 * -1 and simply disables the audio-sync highlight. Everything else works
 * identically for text-only turns.
 *
 * Responsibilities on top of `TranscriptView`:
 *   - Filter `speakerRole === "system"` turns.
 *   - Show a skeleton-bubble loading animation while `data.transcript` is
 *     not yet hydrated (the chat serializer populates fields incrementally
 *     per TH-4525, so we prefer "loading" over a misleading terminal
 *     empty-state).
 */

// Soft pulse for the bubbles so they feel alive next to MUI's skeleton wave.
const pulse = keyframes`
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.65; }
`;

const BUBBLE_WIDTHS = [220, 160, 260, 180, 240];

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
    <Stack
      sx={{
        maxWidth: "70%",
        minWidth: 140,
        gap: 0.5,
      }}
    >
      <Skeleton
        variant="rounded"
        animation="wave"
        height={44}
        width={width}
        sx={{
          borderRadius:
            align === "right"
              ? "12px 12px 4px 12px"
              : "12px 12px 12px 4px",
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
    sx={{
      width: "100%",
      py: 2,
      pr: 1,
      overflow: "hidden",
    }}
  >
    <SkeletonBubble align="left" delay={0} width={BUBBLE_WIDTHS[0]} />
    <SkeletonBubble align="right" delay={120} width={BUBBLE_WIDTHS[1]} />
    <SkeletonBubble align="left" delay={240} width={BUBBLE_WIDTHS[2]} />
    <SkeletonBubble align="right" delay={360} width={BUBBLE_WIDTHS[3]} />
    <SkeletonBubble align="left" delay={480} width={BUBBLE_WIDTHS[4]} />

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

// Pull the text body out of a chat turn. The chat serializer uses
// `messages[0]` as the turn content (see
// `src/sections/test/CallLogs/common.js → getContentMessage`), while the
// voice serializer sets `content` directly. `enrichTurns` inside
// `TranscriptView` only looks at content/message/text — without this
// pre-flatten the voice transcript renderer shows rows with empty bodies.
// Derive a "seconds since start of chat" offset from whatever timestamp
// shape the chat turn carries. TranscriptView's enrichTurns reads
// startTimeSeconds / startTime / start / time / timestamp — chat turns
// usually ship `created_at` (ISO string) per message, which isn't in
// that list. We normalize to epoch-milliseconds here; enrichTurns' own
// epoch-normalization pass then rebases the minimum timestamp to 0
// (that's how voice simulate transcripts are handled), so the first
// chat turn lands at 0:00 and later turns show offsets relative to it.
const extractChatTimestampMs = (turn) => {
  if (!turn) return null;
  const candidates = [
    turn.startTimeSeconds,
    turn.start_time_seconds,
    turn.startTime,
    turn.start_time,
    turn.start,
    turn.timestamp,
    turn.timeStamp,
    turn.created_at,
    turn.createdAt,
    turn.time,
  ];
  for (const raw of candidates) {
    if (raw == null) continue;
    if (typeof raw === "number" && Number.isFinite(raw)) {
      // Interpret as seconds if small (< 10^10), else already ms. Both
      // shapes work with enrichTurns — we just return whatever the first
      // meaningful field is.
      return raw;
    }
    if (typeof raw === "string") {
      // Numeric string?
      const asNum = Number(raw);
      if (Number.isFinite(asNum) && /^\s*-?\d+(\.\d+)?\s*$/.test(raw)) {
        return asNum;
      }
      const parsed = Date.parse(raw);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
};

const extractChatContent = (turn) => {
  if (!turn) return "";
  if (typeof turn.content === "string" && turn.content) return turn.content;
  if (typeof turn.message === "string" && turn.message) return turn.message;
  if (typeof turn.text === "string" && turn.text) return turn.text;
  const messages = turn.messages;
  if (Array.isArray(messages) && messages.length > 0) {
    const first = messages[0];
    if (typeof first === "string") return first;
    if (first && typeof first === "object") {
      return (
        first.content ||
        first.message ||
        first.text ||
        (typeof first.value === "string" ? first.value : "") ||
        ""
      );
    }
  }
  return "";
};

// Rough word count for a chat turn. Chat transcripts don't carry per-turn
// durations, so `TranscriptView.TalkRatioBar` computes 0% for every role
// and the legend dots disappear. Seeding each turn's `duration` with its
// word count gives the shared code a non-zero signal to work with, so the
// row reads (semantically correctly) "Customer 55% · Assistant 45%" —
// i.e. share of words instead of share of speaking time. This is also
// where the inline color legend comes from: the TalkRatioBar renders a
// colored dot + role label + percentage per speaker.
const countWords = (text) => {
  if (!text || typeof text !== "string") return 0;
  return text.trim().split(/\s+/).filter(Boolean).length;
};

const ChatTranscriptView = ({ data }) => {
  const filteredTranscript = useMemo(() => {
    const transcript = data?.transcript;
    if (!Array.isArray(transcript)) return [];
    return transcript
      .filter((item) => item.speakerRole !== "system")
      .map((item) => {
        const ts = extractChatTimestampMs(item);
        const content = extractChatContent(item);
        const existingDuration =
          typeof item.duration === "number" && Number.isFinite(item.duration)
            ? item.duration
            : null;
        return {
          ...item,
          // Flatten chat-shaped `messages[0]` into a plain `content`
          // field so TranscriptView's enrichTurns/getContent picks it up.
          content,
          // Synthesize a `startTimeSeconds` in enrichTurns' native shape
          // so it renders a turn timestamp next to each row. The epoch
          // -> offset-seconds normalization inside enrichTurns rebases
          // the earliest value to 0, so the first turn reads 0:00 and
          // later turns show clock offsets from it.
          ...(ts != null ? { startTimeSeconds: ts } : {}),
          // Seed a word-count "duration" so TalkRatioBar can render
          // the inline color legend (dot + role + %). Only fall back to
          // this when the turn doesn't already carry a real duration.
          ...(existingDuration == null
            ? { duration: countWords(content) }
            : {}),
        };
      });
  }, [data?.transcript]);

  // No terminal empty-state for now. The chat simulation serializer is
  // still in flux (TH-4525) and today frequently returns no transcript
  // on completed chats even though the conversation actually has
  // messages. Showing a "No messages in this chat" terminal state there
  // is misleading; the skeleton loader is both accurate (data is still
  // being populated) and kinder to the user. Revisit once TH-4525 lands
  // and `transcript: []` on a completed chat is reliably meaningful.
  if (filteredTranscript.length === 0) {
    return <TranscriptLoading />;
  }

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
      <TranscriptView
        transcript={filteredTranscript}
        // Repurpose TranscriptView's TalkRatioBar as a compact speaker
        // legend: no "TALK RATIO" label, no percentages, left-aligned
        // (reads as a simple speaker-color legend). Audio-only timeline
        // strip is also hidden (chat has no speaker timeline) along
        // with the voice-specific "Xs silence" inline markers.
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
