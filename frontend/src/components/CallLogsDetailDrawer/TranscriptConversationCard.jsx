import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { formatDuration } from "src/utils/format-time";
import { format } from "date-fns";
import { formatRole } from "src/sections/test/common";

const ColorMap = (role) => {
  switch (role) {
    case "system": {
      return {
        borderColor: "status.warning.border",
        backgroundColor: "status.warning.bg",
        headerColor: "status.warning.solid",
        textColor: "status.warning.fg",
      };
    }
    case "user": {
      return {
        borderColor: "status.success.border",
        backgroundColor: "status.info.bg",
        headerColor: "status.success.solid",
        textColor: "status.success.fg",
      };
    }
    case "agent":
    case "bot": {
      return {
        borderColor: "status.info.border",
        backgroundColor: "status.warning.bg",
        headerColor: "status.info.solid",
        textColor: "status.info.fg",
      };
    }
    default: {
      return {
        borderColor: "background.default",
        backgroundColor: "background.default",
        headerColor: "text.disabled",
        textColor: "text.secondary",
      };
    }
  }
};

const TranscriptConversationCard = ({
  role,
  duration,
  content,
  align,
  timeStamp,
  agentName,
  simulatorName,
}) => {
  return (
    <Box
      sx={{
        display: "flex",
        maxWidth: "80%",
        backgroundColor: ColorMap(role)?.backgroundColor,
        borderRadius: "4px",
        padding: 1,
        flexDirection: "column",
        gap: 1,
        alignSelf: align,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          justifyContent: "space-between",
          width: "100%",
        }}
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          sx={{ color: "text.primary" }}
        >
          {formatRole(role, agentName, simulatorName)}
        </Typography>
        <Typography typography="s2" color="text.disabled">
          {formatDuration(duration)}
        </Typography>
      </Box>
      <Box>
        <Typography typography="s2" color={"text.disabled"}>
          {content}
        </Typography>
      </Box>
      <Box>
        <Typography typography="s2" color="text.disabled">
          {format(new Date(timeStamp), "h:mm a 'on' MM/dd/yyyy")}
        </Typography>
      </Box>
    </Box>
  );
};

TranscriptConversationCard.propTypes = {
  role: PropTypes.string,
  duration: PropTypes.number,
  content: PropTypes.string,
  align: PropTypes.string,
  timeStamp: PropTypes.string,
  agentName: PropTypes.string,
  simulatorName: PropTypes.string,
};

export default TranscriptConversationCard;
