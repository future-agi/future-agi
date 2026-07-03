import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import TranscriptConversationCard from "./TranscriptConversationCard";

const CallLogTranscript = ({ data }) => {
  const filteredTranscript = useMemo(() => {
    const transcript = data?.transcripts;
    return transcript?.filter((item) => item.speaker_role !== "system");
  }, [data]);
  return (
    <Stack
      gap={1}
      overflow="auto"
      flexGrow={1}
      border="1px solid"
      borderColor="divider"
      borderRadius={0.5}
      width={"100%"}
      p={1}
    >
      {filteredTranscript?.length === 0 ? (
        <Stack minHeight={200} alignItems={"center"} justifyContent={"center"}>
          <Typography typography="s2_1" fontWeight={"fontWeightMedium"}>
            Transcript is empty - <i>{data?.endedReason}</i>
          </Typography>
        </Stack>
      ) : (
        filteredTranscript?.map((item) => (
          <TranscriptConversationCard
            key={item.id}
            role={item.role}
            content={item.content}
            simulatorName={data?.customer_name}
            agentName={"User/FAGI Simulator"}
            duration={Math.round(item.duration)}
            align={item.role === "user" ? "flex-end" : "flex-start"}
            timeStamp={item.time}
          />
        ))
      )}
    </Stack>
  );
};

CallLogTranscript.propTypes = {
  data: PropTypes.object.isRequired,
};

export default CallLogTranscript;
