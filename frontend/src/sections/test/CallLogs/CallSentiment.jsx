import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { CallSentimentOptions } from "./common";

const sentimentStyles = {
  [CallSentimentOptions.POSITIVE]: {
    style: {
      color: "status.success.fg",
    },
    text: "Positive",
  },
  [CallSentimentOptions.NEGATIVE]: {
    style: {
      color: "status.danger.fg",
    },
    text: "Negative",
  },
  [CallSentimentOptions.NEUTRAL]: {
    style: {
      color: "status.warning.fg",
    },
    text: "Neutral",
  },
};

const CallSentiment = ({ sentiment }) => {
  return (
    <Box
      sx={{
        ...sentimentStyles[sentiment].style,
        padding: "4px 8px",
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        typography: "s3",
        fontWeight: "fontWeightMedium",
      }}
    >
      {sentimentStyles[sentiment].text}
    </Box>
  );
};

CallSentiment.propTypes = {
  sentiment: PropTypes.string,
};

export default CallSentiment;
