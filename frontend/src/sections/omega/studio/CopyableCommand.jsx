import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import { alpha } from "@mui/material/styles";

import StudioIcon from "./StudioIcon";

const MONO =
  'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace';

// One shell command in a quiet code block, with a copy button that confirms
// with a check for a moment. It wraps only between words, never inside a
// flag, and is built from inline elements so it can sit inside a sentence's
// paragraph.
export default function CopyableCommand({ command, testId }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  const textRef = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  const selectText = () => {
    const node = textRef.current;
    const selection = window.getSelection?.();
    if (!node || !selection) return;
    const range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      // No clipboard permission: select the text so Cmd/Ctrl+C works.
      selectText();
    }
  };

  return (
    <Box
      component="span"
      data-testid={testId}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        pl: 1.5,
        pr: 0.5,
        py: 0.5,
        minWidth: 0,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: (theme) =>
          theme.palette.mode === "light"
            ? alpha(theme.palette.grey[500], 0.06)
            : alpha(theme.palette.common.white, 0.04),
      }}
    >
      <Box
        component="span"
        aria-hidden
        sx={{ fontFamily: MONO, fontSize: 13, color: "text.disabled" }}
      >
        $
      </Box>
      <Box
        component="code"
        ref={textRef}
        data-testid={testId ? `${testId}-text` : undefined}
        sx={{
          flex: 1,
          minWidth: 0,
          fontFamily: MONO,
          fontSize: 13,
          lineHeight: "20px",
          color: "text.primary",
          whiteSpace: "normal",
          py: 0.5,
        }}
      >
        {command.split(" ").map((word, index) => (
          <React.Fragment key={`${word}-${index}`}>
            {index > 0 ? " " : null}
            <Box component="span" sx={{ whiteSpace: "nowrap" }}>
              {word}
            </Box>
          </React.Fragment>
        ))}
      </Box>
      <Tooltip title={copied ? "Copied" : "Copy command"}>
        <IconButton
          size="small"
          onClick={copy}
          aria-label="Copy command"
          data-testid={testId ? `${testId}-copy` : undefined}
          sx={{ color: copied ? "success.main" : "text.secondary" }}
        >
          <StudioIcon name={copied ? "check" : "copy"} size={16} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

CopyableCommand.propTypes = {
  command: PropTypes.string.isRequired,
  testId: PropTypes.string,
};
