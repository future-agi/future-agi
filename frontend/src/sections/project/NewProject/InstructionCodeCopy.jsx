import { Box, IconButton } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "src/components/snackbar";
import { primaryFont } from "src/theme/typography";

const InstructionCodeCopy = ({
  ariaLabel = "Copy code",
  text,
  language,
  onCopy,
}) => {
  const onCopyClick = () => {
    copyToClipboard(text);
    enqueueSnackbar("Copied to clipboard", { variant: "success" });
    if (onCopy) {
      onCopy();
    }
  };
  return (
    <Box sx={{ position: "relative", maxWidth: "100%" }}>
      <IconButton
        aria-label={ariaLabel}
        sx={{ position: "absolute", right: "2px", top: "8px", zIndex: 1 }}
        onClick={onCopyClick}
      >
        <Iconify icon="basil:copy-outline" sx={{ color: "text.disabled" }} />
      </IconButton>
      <SyntaxHighlighter
        useInlineStyles={false}
        customStyle={{
          fontSize: "12px",
          borderRadius: "8px",
          textAlign: "left",
          border: "1px solid var(--border-default)",
          padding: "10px",
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
          whiteSpace: "pre", // Maintain code formatting
          wordWrap: "normal",
          wordBreak: "normal",
          boxSizing: "border-box",
        }}
        codeTagProps={{
          style: {
            color: "var(--text-primary)",
            textShadow: "none",
            background: "transparent",
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
  ariaLabel: PropTypes.string,
  text: PropTypes.string,
  language: PropTypes.string,
  onCopy: PropTypes.func,
};

export default InstructionCodeCopy;
