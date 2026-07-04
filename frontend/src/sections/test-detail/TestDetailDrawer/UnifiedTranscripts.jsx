import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import ConversationCard from "./ConversationCard";
import { getContentMessage } from "src/sections/test/CallLogs/common";

const UnifiedCallTranscript = ({
  endReason,
  transcript,
  agentName,
  simulatorName,
  callType,
  simulationCallType,
}) => {
  const getItemProps = (item) => {
    const role = item.speaker_role || item.role;

    let duration;

    if (item.duration !== undefined) {
      duration = Math.round(item.duration);
    } else if (item.startTimeSeconds && item.endTimeSeconds) {
      duration = Math.round(item.endTimeSeconds - item.startTimeSeconds);
    }

    // For voice transcripts prefer the recording-aligned elapsed time;
    // fall back to the DB-written wall-clock timestamp for chat messages.
    const timeStamp =
      item.startTimeSeconds != null
        ? item.startTimeSeconds
        : item.created_at || item.time;
    const content = getContentMessage(item, simulationCallType);
    return {
      role,
      duration,
      timeStamp,
      content,
      align: role === "user" ? "flex-end" : "flex-start",
    };
  };

  return (
    <Stack
      gap={1.5}
      overflow="auto"
      flexGrow={1}
      borderRadius={0.5}
      width="100%"
      sx={{
        display: "flex",
        flexDirection: "column",
      }}
    >
      {transcript?.length === 0 ? (
        <Stack
          minHeight={200}
          alignItems="center"
          justifyContent="center"
          spacing={1}
          sx={{
            border: "1px dashed",
            borderColor: "divider",
            borderRadius: 1,
            p: 3,
            backgroundColor: (theme) =>
              theme.palette.mode === "dark"
                ? "rgba(255,255,255,0.02)"
                : "grey.50",
          }}
        >
          <Iconify
            icon="mdi:message-text-outline"
            width={36}
            sx={{ color: "text.disabled" }}
          />
          <Typography
            typography="s2_1"
            fontWeight="fontWeightMedium"
            color="text.secondary"
          >
            No transcript available
          </Typography>
          {endReason && (
            <Box
              sx={{
                px: 1.5,
                py: 0.5,
                borderRadius: 1,
                backgroundColor: "warning.lighter",
                color: "warning.darker",
                maxWidth: "80%",
              }}
            >
              <Typography variant="caption" sx={{ wordBreak: "break-word" }}>
                {endReason}
              </Typography>
            </Box>
          )}
        </Stack>
      ) : (
        transcript?.map((item) => {
          const itemProps = getItemProps(item);
          return (
            <ConversationCard
              key={item.id}
              role={itemProps.role}
              align={itemProps.align}
              content={itemProps.content}
              duration={itemProps.duration}
              timeStamp={itemProps.timeStamp}
              agentName={agentName}
              simulatorName={simulatorName}
              callType={callType}
              simulationCallType={simulationCallType}
            />
          );
        })
      )}
    </Stack>
  );
};

UnifiedCallTranscript.propTypes = {
  endReason: PropTypes.string,

  transcript: PropTypes.array,
  agentName: PropTypes.string,
  simulatorName: PropTypes.string,
  callType: PropTypes.string,
  simulationCallType: PropTypes.string,
};

export default UnifiedCallTranscript;
