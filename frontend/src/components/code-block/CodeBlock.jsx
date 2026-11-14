import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

// Design System code block: monospace 12/18 on bg.muted with a hairline,
// a copy button top-right. `wrap` for prose (prompts), off for code.
// `copyText` copies something other than what's shown — a snippet that
// displays keys masked copies the real ones.
export default function CodeBlock({ code, wrap = false, onCopy, copyText }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(copyText ?? code);
    onCopy?.();
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Box
      sx={{
        position: "relative",
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.muted",
      }}
    >
      <Tooltip title={copied ? "Copied" : "Copy"}>
        <IconButton
          size="small"
          onClick={copy}
          aria-label="Copy code"
          sx={{ position: "absolute", top: 6, right: 6 }}
        >
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={16}
          />
        </IconButton>
      </Tooltip>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1.5,
          pr: 5,
          fontFamily: "monospace",
          fontSize: 12,
          lineHeight: "18px",
          color: "text.primary",
          overflowX: "auto",
          whiteSpace: wrap ? "pre-wrap" : "pre",
        }}
      >
        {code}
      </Box>
    </Box>
  );
}

CodeBlock.propTypes = {
  code: PropTypes.string.isRequired,
  wrap: PropTypes.bool,
  onCopy: PropTypes.func,
  copyText: PropTypes.string,
};
