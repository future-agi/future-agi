import EmptyState from "src/components/empty-state";
import React from "react";
import PropTypes from "prop-types";
import { Stack } from "@mui/material";
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
      rawContent: Array.isArray(item.content) ? item.content : [],
      align: role === "user" ? "flex-end" : "flex-start",
    };
  };

  return (
    <Stack
      gap={1.5}
      overflow="auto"
      flexGrow={1}
      borderRadius="4px"
      width="100%"
      sx={{
        display: "flex",
        flexDirection: "column",
      }}
    >
      {transcript?.length === 0 ? (
        <EmptyState
          icon="solar:chat-round-line-linear"
          title="No transcript yet"
          description={endReason || undefined}
          sx={{ pt: 4, pb: 4 }}
        />
      ) : (
        transcript?.map((item) => {
          const itemProps = getItemProps(item);
          return (
            <ConversationCard
              key={item.id}
              role={itemProps.role}
              align={itemProps.align}
              content={itemProps.content}
              rawContent={itemProps.rawContent}
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
