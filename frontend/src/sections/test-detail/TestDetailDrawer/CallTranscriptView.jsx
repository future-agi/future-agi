import { Box } from "@mui/material";
import React from "react";
import ConversationCard from "./ConversationCard";
import PropTypes from "prop-types";

const CallTranscriptView = ({
  transcript,
  agentName,
  simulatorName,
  callType,
}) => {
  return (
    <Box
      sx={{
        overflow: "auto",
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        width: "100%",
      }}
    >
      {transcript?.map((item) => (
        <ConversationCard
          key={item.id}
          role={item.speaker_role}
          align={item.speaker_role === "user" ? "flex-end" : "flex-start"}
          content={item.content}
          duration={Math.floor(
            (item.endTimeSeconds - item.startTimeSeconds) / 1000,
          )}
          timeStamp={
            item.startTimeSeconds != null
              ? item.startTimeSeconds
              : item.created_at
          }
          agentName={agentName}
          simulatorName={simulatorName}
          callType={callType}
        />
      ))}
    </Box>
  );
};

CallTranscriptView.propTypes = {
  transcript: PropTypes.array,
  agentName: PropTypes.string,
  simulatorName: PropTypes.string,
  callType: PropTypes.string,
};

export default CallTranscriptView;
