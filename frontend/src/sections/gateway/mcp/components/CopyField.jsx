import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";

import McpIcon from "./McpIcon";
import { GATEWAY_MONO } from "../../utils/fonts";

// A value to paste somewhere else (an endpoint, an id), with a copy button.
export default function CopyField({ value, label }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        minWidth: 0,
        pl: 1.25,
        pr: 0.25,
        py: 0.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Box
        component="code"
        sx={{
          flex: 1,
          minWidth: 0,
          fontFamily: GATEWAY_MONO,
          fontSize: 12,
          color: "text.primary",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={value}
      >
        {value}
      </Box>
      <Tooltip title={copied ? "Copied" : "Copy"}>
        <IconButton size="small" aria-label={`Copy ${label}`} onClick={copy}>
          <McpIcon name={copied ? "check" : "copy"} size={16} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

CopyField.propTypes = {
  value: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
};
