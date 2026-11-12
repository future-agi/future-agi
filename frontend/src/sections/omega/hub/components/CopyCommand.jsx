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

// One word of a command, cut after each "/" and ":" that a name follows (so
// never inside "://"). The pieces are shown whole; a line may break between
// them, so a reference or a registry URL wraps at a path boundary instead of
// in the middle of a name.
export function wordPieces(word) {
  const pieces = [];
  let start = 0;
  for (let index = 0; index < word.length - 1; index += 1) {
    const char = word[index];
    if ((char === "/" || char === ":") && word[index + 1] !== "/") {
      pieces.push(word.slice(start, index + 1));
      start = index + 1;
    }
  }
  pieces.push(word.slice(start));
  return pieces;
}

// A line of a command as text with <wbr> break points: spaces stay ordinary
// spaces, so the text, its selection and its copy are the command itself.
function CommandLine({ line }) {
  return line.split(/(\s+)/).map((word, wordIndex) => {
    if (!word || /^\s+$/.test(word)) return word;
    return wordPieces(word).map((piece, pieceIndex) => (
      <React.Fragment key={`${wordIndex}-${pieceIndex}`}>
        {pieceIndex > 0 && <wbr />}
        {piece}
      </React.Fragment>
    ));
  });
}

CommandLine.propTypes = { line: PropTypes.string.isRequired };

// A copy button for one value (a key name, a reference): the same icon,
// tooltip and "Copied" confirmation as a command's. `tooltip` is a shorter
// hover text where the label names the value the button sits beside.
export function CopyButton({ text, label, tooltip, size = 14, testId, sx }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    if (await copyText(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <Tooltip title={copied ? "Copied" : tooltip || label || "Copy"}>
      <IconButton
        size="small"
        onClick={onCopy}
        aria-label={label || `Copy ${text}`}
        data-testid={testId}
        sx={{ flexShrink: 0, ...sx }}
      >
        <HubIcon name={copied ? "check" : "copy"} size={size} />
      </IconButton>
    </Tooltip>
  );
}

CopyButton.propTypes = {
  text: PropTypes.string.isRequired,
  label: PropTypes.string,
  tooltip: PropTypes.string,
  size: PropTypes.number,
  testId: PropTypes.string,
  sx: PropTypes.object,
};

// One copyable command or reference, in the theme's mono face, with a copy
// button.
// The whole command is always on screen: one too long for its box wraps at
// a space, or after a "/" or ":" inside a long reference or URL, never by
// cutting a name, so a reader can check every word before running it. Each
// line of a multi-line command (an `export` before a run) has its own "$"
// prompt, which is not part of the selection. The copy, the title and a
// text selection all give the same full text.
export default function CopyCommand({ text, label, prompt = "$", testId }) {
  const lines = String(text).split("\n");
  return (
    <Box
      data-testid={testId}
      sx={{
        display: "flex",
        alignItems: "flex-start",
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
      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          py: 0.5,
          display: "flex",
          flexDirection: "column",
        }}
      >
        {lines.map((line, index) => (
          <Box key={index} sx={{ display: "flex", gap: 1, minWidth: 0 }}>
            {prompt && (
              <Box
                component="span"
                aria-hidden="true"
                sx={{
                  typography: "code_md",
                  color: "text.tertiary",
                  flexShrink: 0,
                  userSelect: "none",
                }}
              >
                {prompt}
              </Box>
            )}
            <Box
              component="code"
              title={line}
              data-command={line}
              sx={{
                flex: 1,
                minWidth: 0,
                typography: "code_md",
                color: "text.primary",
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
              }}
            >
              <CommandLine line={line} />
            </Box>
          </Box>
        ))}
      </Box>
      <CopyButton text={text} label={label} />
    </Box>
  );
}

CopyCommand.propTypes = {
  text: PropTypes.string.isRequired,
  label: PropTypes.string,
  prompt: PropTypes.string,
  testId: PropTypes.string,
};
