/* eslint-disable react-refresh/only-export-components */
import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";

import HubIcon from "./HubIcon";

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

// Where a command is cut when it does not fit: everything after the last "/"
// (the entry and version), at most TAIL_MAX characters, always stays visible.
const TAIL_MAX = 28;

export function splitForEllipsis(text) {
  const slash = text.lastIndexOf("/");
  const cut = Math.max(slash + 1, text.length - TAIL_MAX);
  return cut > 0 && cut < text.length
    ? [text.slice(0, cut), text.slice(cut)]
    : [text, ""];
}

// One copyable command or reference, in the mono face, with a copy button. A
// command too long for its box loses its middle, never its end (HUB-23); the
// full text is in the tooltip, the copy and a text selection.
export default function CopyCommand({ text, label, prompt = "$", testId }) {
  const [head, tail] = splitForEllipsis(text);
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    if (await copyText(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <Box
      data-testid={testId}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        pl: 1.25,
        pr: 0.5,
        py: 0.5,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        minWidth: 0,
      }}
    >
      {prompt && (
        <Box
          component="span"
          sx={{ color: "text.tertiary", fontFamily: "monospace", fontSize: 12 }}
        >
          {prompt}
        </Box>
      )}
      <Box
        component="code"
        title={text}
        data-command={text}
        sx={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          overflow: "hidden",
          fontFamily:
            "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 12,
          color: "text.primary",
          whiteSpace: "nowrap",
        }}
      >
        <Box
          component="span"
          sx={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}
        >
          {head}
        </Box>
        {tail && (
          <Box component="span" sx={{ flexShrink: 0 }}>
            {tail}
          </Box>
        )}
      </Box>
      <Tooltip title={copied ? "Copied" : label || "Copy"}>
        <IconButton
          size="small"
          onClick={onCopy}
          aria-label={label || `Copy ${text}`}
        >
          <HubIcon name={copied ? "check" : "copy"} size={14} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

CopyCommand.propTypes = {
  text: PropTypes.string.isRequired,
  label: PropTypes.string,
  prompt: PropTypes.string,
  testId: PropTypes.string,
};
