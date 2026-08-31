import { useState } from "react";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import { oneDark, oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import { ALK_MONO } from "../alkTokens";

SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("sql", sql);
SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("javascript", javascript);
SyntaxHighlighter.registerLanguage("yaml", yaml);

/** The language a file's own name declares, for callers that hold a path rather than a language. */
export const languageOf = (path) => {
  const extension = String(path || "").split(".").pop().toLowerCase();
  return (
    {
      py: "python",
      json: "json",
      sql: "sql",
      sh: "bash",
      bash: "bash",
      js: "javascript",
      jsx: "javascript",
      yml: "yaml",
      yaml: "yaml",
    }[extension] || ""
  );
};

/**
 * Source the operator will want out of the page — handler code, scenario files, a check's
 * body. The copy button stays hidden until the block is hovered or focused, so a page full
 * of these is not a page full of buttons.
 */
const CodeBlock = ({ children, wrap, language }) => {
  const [said, setSaid] = useState("copy");
  const theme = useTheme();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(String(children ?? ""));
      setSaid("copied");
    } catch {
      setSaid("select + ⌘C");
    }
    setTimeout(() => setSaid("copy"), 1400);
  };

  const frame = {
    m: 0,
    px: 1,
    py: 0.9,
    pr: 5,
    bgcolor: "background.default",
    border: "1px solid",
    borderColor: "divider",
    borderRadius: 1,
    fontFamily: ALK_MONO,
    fontSize: 11.6,
    lineHeight: 1.55,
    color: "text.primary",
    overflowX: wrap ? "visible" : "auto",
    whiteSpace: wrap ? "pre-wrap" : "pre",
    overflowWrap: wrap ? "anywhere" : "normal",
  };

  return (
    <Box
      sx={{
        position: "relative",
        "&:hover .alk-copy, &:focus-within .alk-copy": { opacity: 1 },
      }}
    >
      {language ? (
        <Box
          sx={{
            ...frame,
            p: 0,
            // The theme's own background loses to the card's, so every block sits on the
            // same surface whether or not it is highlighted.
            "& pre": {
              m: "0 !important",
              px: 1,
              py: 0.9,
              pr: 5,
              background: "transparent !important",
              fontFamily: `${ALK_MONO} !important`,
              fontSize: "11.6px !important",
              lineHeight: "1.55 !important",
              overflowX: wrap ? "visible" : "auto",
              whiteSpace: wrap ? "pre-wrap !important" : "pre",
              overflowWrap: wrap ? "anywhere" : "normal",
            },
            "& code": {
              fontFamily: `${ALK_MONO} !important`,
              fontSize: "inherit !important",
              background: "transparent !important",
            },
          }}
        >
          <SyntaxHighlighter
            language={language}
            style={theme.palette.mode === "dark" ? oneDark : oneLight}
          >
            {String(children ?? "")}
          </SyntaxHighlighter>
        </Box>
      ) : (
        <Box component="pre" sx={frame}>
          {children}
        </Box>
      )}
      <Box
        className="alk-copy"
        component="button"
        type="button"
        onClick={copy}
        sx={{
          position: "absolute",
          top: 6,
          right: 6,
          px: 0.75,
          py: 0.2,
          opacity: 0,
          transition: "opacity 120ms",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          background: (thm) => thm.palette.background.paper,
          color: "text.secondary",
          fontFamily: ALK_MONO,
          fontSize: 10.5,
          cursor: "pointer",
          "&:focus-visible": { opacity: 1 },
        }}
      >
        {said}
      </Box>
    </Box>
  );
};

CodeBlock.propTypes = {
  children: PropTypes.node,
  wrap: PropTypes.bool,
  language: PropTypes.string,
};

export default CodeBlock;
