import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import TranscriptView from "src/components/VoiceDetailDrawerV2/TranscriptView";
import { AGENT_TYPES } from "src/sections/agents/constants";
import {
  countWords,
  getChatTurnContent,
  getChatTurnTimestampMs,
} from "src/components/ChatDetailDrawerV2/chatTranscriptUtils";


const TranscriptEmpty = () => (
  <Stack
    alignItems="center"
    justifyContent="center"
    spacing={1}
    sx={{ flex: 1, py: 6, color: "text.secondary" }}
  >
    <Iconify icon="mdi:message-text-outline" width={28} />
    <Typography typography="s2" color="text.secondary">
      No transcript available
    </Typography>
  </Stack>
);

const ViewFullTranscript = ({
  open,
  onClose,
  transcript,
  simulationCallType,
}) => {
  const isChat = simulationCallType === AGENT_TYPES.CHAT;

  const turns = useMemo(() => {
    if (!Array.isArray(transcript)) return [];
    if (!isChat) return transcript;

    return transcript.map((item) => {
      const content = getChatTurnContent(item);
      const ts = getChatTurnTimestampMs(item);
      const existingDuration =
        typeof item.duration === "number" && Number.isFinite(item.duration)
          ? item.duration
          : null;
      return {
        ...item,
        content,

        rawMessages: Array.isArray(item.content) ? item.content : null,
        ...(ts != null ? { startTimeSeconds: ts } : {}),
        ...(existingDuration == null ? { duration: countWords(content) } : {}),
      };
    });
  }, [transcript, isChat]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ display: "flex", justifyContent: "space-between" }}>
        Call Transcript
        <IconButton onClick={onClose}>
          <Iconify icon="akar-icons:cross" width={16} height={16} />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ overflow: "hidden" }}>
        <Box sx={{ display: "flex", height: "60vh", minHeight: 0 }}>
          {turns.length === 0 ? (
            <TranscriptEmpty />
          ) : (
            <TranscriptView
              transcript={turns}
              embedded
              hideTimelineStrip={isChat}
              hideTalkRatioLabel={isChat}
              hideTalkRatioPercentages={isChat}
              hideSilenceMarkers={isChat}
              hideTurnDurations={isChat}
              hideInterruptBadges={isChat}
            />
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button
          variant="outlined"
          size="small"
          onClick={onClose}
          sx={{ lineHeight: 1 }}
        >
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

ViewFullTranscript.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  transcript: PropTypes.array,
  simulationCallType: PropTypes.string,
};

export default ViewFullTranscript;
