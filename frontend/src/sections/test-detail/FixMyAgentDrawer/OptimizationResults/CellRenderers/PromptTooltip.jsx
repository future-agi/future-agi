import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import MarkdownWithVariableHighlight from "src/components/ReactMarkdownWithHighlight";

const PromptTooltip = ({ value }) => {
  return (
    <Box
      sx={{
        maxHeight: "200px",
        width: "400px",
        backgroundColor: "background.paper",
        borderRadius: "6px",
        border: "1px solid",
        borderColor: "divider",
        padding: 2,
        overflowY: "auto",
      }}
    >
      <MarkdownWithVariableHighlight content={value || ""} />
    </Box>
  );
};

PromptTooltip.propTypes = {
  value: PropTypes.string,
};

export default PromptTooltip;
