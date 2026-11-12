import { Box, IconButton } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { usePersonalKeyText } from "src/components/personal-key-snippet-context";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import Iconify from "src/components/iconify";
import { copyText } from "src/sections/omega/code/clipboard";
import { enqueueSnackbar } from "src/components/snackbar";
import { primaryFont } from "src/theme/typography";
import { NO_RECORDING_CLASS } from "src/sections/omega/code/noRecording";

const InstructionCodeCopy = ({ text: template, language, onCopy }) => {
  const fill = usePersonalKeyText();
  const text = fill(template);
  const copyRoot = React.useRef(null);
  const onCopyClick = async () => {
    const copied = await copyText(text, copyRoot.current);
    enqueueSnackbar(
      copied
        ? "Copied to clipboard"
        : "Could not copy. Select and copy the code.",
      { variant: copied ? "success" : "error" },
    );
    if (copied) onCopy?.();
  };
  return (
    <Box
      className={NO_RECORDING_CLASS}
      ref={copyRoot}
      sx={{ position: "relative", maxWidth: "100%" }}
    >
      <IconButton
        aria-label="Copy code"
        sx={{ position: "absolute", right: "2px", top: "8px", zIndex: 1 }}
        onClick={onCopyClick}
      >
        <Iconify icon="solar:copy-linear" sx={{ color: "text.disabled" }} />
      </IconButton>
      <SyntaxHighlighter
        useInlineStyles={false}
        customStyle={{
          fontSize: "12px",
          borderRadius: "8px",
          textAlign: "left",
          border: "1px solid var(--border-default)",
          // Keep the right edge clear for the copy button over the first line.
          padding: "10px 40px 10px 10px",
          backgroundColor: "var(--bg-neutral)",

          color: "var(--text-primary)",
          textShadow: "none",
          fontFamily: primaryFont,
          margin: "0px",
          // Add overflow handling
          overflowX: "auto",
          overflowY: "hidden",
          maxWidth: "100%",
          minWidth: 0,
          // Keep indentation but wrap long lines inside the box, so a sample
          // in a narrow card stays readable. Copy uses the original text.
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          wordBreak: "normal",
          boxSizing: "border-box",
        }}
        codeTagProps={{
          style: {
            color: "var(--text-primary)",
            textShadow: "none",
            background: "transparent",
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
          },
        }}
        language={language}
      >
        {text}
      </SyntaxHighlighter>
    </Box>
  );
};

InstructionCodeCopy.propTypes = {
  text: PropTypes.string,
  language: PropTypes.string,
  onCopy: PropTypes.func,
};

export default InstructionCodeCopy;
