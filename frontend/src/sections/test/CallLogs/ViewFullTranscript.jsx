import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import _ from "lodash";
import ConversationCard from "../../test-detail/TestDetailDrawer/ConversationCard";
import { ShowComponent } from "../../../components/show";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { getContentMessage } from "./common";

const ViewFullTranscript = ({
  open,
  onClose,
  transcript,
  agentName,
  simulatorName,
  simulationCallType,
}) => {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: "flex", justifyContent: "space-between" }}>
        Call Transcript
        <IconButton onClick={onClose}>
          <Iconify icon="akar-icons:cross" width={16} height={16} />
        </IconButton>
      </DialogTitle>
      <DialogContent sx={{ maxHeight: 300, overflowY: "auto" }}>
        <Box
          sx={{
            borderRadius: 1,

            gap: 1.5,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <ShowComponent condition={transcript?.length === 0}>
            <Typography typography="s1" fontWeight="fontWeightSemiBold">
              No transcript available
            </Typography>
          </ShowComponent>
          {transcript?.map(
            ({
              id,
              speaker_role: speakerRole,
              content: rawContent,
              messages,
              createdAt,
              endTimeSeconds,
              startTimeSeconds,
              role,
            }) => {
              const content = getContentMessage(
                simulationCallType === AGENT_TYPES.CHAT
                  ? { messages }
                  : { rawContent },
                simulationCallType,
              );

              return (
                <ConversationCard
                  key={id}
                  role={speakerRole ?? role}
                  content={content}
                  align={speakerRole === "user" ? "flex-end" : "flex-start"}
                  timeStamp={createdAt}
                  duration={Math.floor(
                    (endTimeSeconds - startTimeSeconds) / 1000,
                  )}
                  agentName={agentName}
                  simulatorName={simulatorName}
                  simulationCallType={simulationCallType}
                />
              );
            },
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
  agentName: PropTypes.string,
  simulatorName: PropTypes.string,
  simulationCallType: PropTypes.string,
};

export default ViewFullTranscript;
