import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Iconify from "src/components/iconify";

function CodeBlockWrapper({ children, className }) {
  const [copied, setCopied] = useState(false);

  // Extract language from className (e.g., "language-js")
  const match = /language-(\w+)/.exec(className || "");
  const language = match ? match[1] : "";

  const handleCopy = useCallback(() => {
    const text = String(children).replace(/\n$/, "");
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [children]);

  // Inline code (no className)
  if (!className) {
    return (
      <Box
        component="code"
        sx={{
          fontFamily: "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
          fontSize: "0.875em",
          bgcolor: "background.neutral",
          color: "primary.main",
          borderRadius: "4px",
          px: 0.75,
          py: 0.25,
        }}
      >
        {children}
      </Box>
    );
  }

  // Code block
  return (
    <Box
      sx={{
        position: "relative",
        my: 1.5,
        border: 1,
        borderColor: "border.default",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      {/* Header bar */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 1.5,
          py: 0.75,
          bgcolor: "background.neutral",
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Box
          component="span"
          sx={{
            fontSize: 12,
            fontFamily: "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
            color: "text.disabled",
            textTransform: "none",
            letterSpacing: 0,
          }}
        >
          {language}
        </Box>
        <IconButton
          size="small"
          onClick={handleCopy}
          sx={{
            color: "text.disabled",
            "&:hover": { color: "text.secondary" },
          }}
          title={copied ? "Copied!" : "Copy code"}
        >
          <Iconify
            icon={copied ? "solar:check-linear" : "solar:copy-linear"}
            width={14}
          />
        </IconButton>
      </Box>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1.5,
          overflow: "auto",
          fontSize: 13,
          lineHeight: 1.6,
          fontFamily: "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
          bgcolor: "background.paper",
          color: "text.primary",
        }}
      >
        <code>{children}</code>
      </Box>
    </Box>
  );
}

CodeBlockWrapper.propTypes = {
  children: PropTypes.node,
  className: PropTypes.string,
};

export default function TextBlock({ content }) {
  if (!content) return null;

  return (
    <Box
      sx={{
        fontSize: 13,
        lineHeight: 1.7,
        color: "text.primary",
        wordBreak: "break-word",
        overflowWrap: "break-word",
        "& p": {
          m: 0,
          mb: 1.25,
        },
        "& p:last-child": { mb: 0 },
        // Headings
        "& h1, & h2, & h3, & h4, & h5, & h6": {
          mt: 2,
          mb: 1,
          fontWeight: 600,
          lineHeight: 1.3,
          color: "text.primary",
        },
        "& h1": { fontSize: 20 },
        "& h2": { fontSize: 16 },
        "& h3": { fontSize: 13 },
        // Tables
        "& table": {
          borderCollapse: "collapse",
          width: "100%",
          my: 1.5,
          fontSize: 13,
          borderRadius: "8px",
          overflow: "hidden",
          border: 1,
          borderColor: "border.default",
        },
        "& th": {
          bgcolor: "background.neutral",
          fontWeight: 600,
          px: 1.5,
          py: 1,
          textAlign: "left",
          fontSize: 12,
          color: "text.secondary",
          borderBottom: 1,
          borderColor: "border.default",
        },
        "& td": {
          px: 1.5,
          py: 0.75,
          borderBottom: 1,
          borderColor: "divider",
          fontSize: 13,
          color: "text.primary",
        },
        "& tr:last-child td": {
          borderBottom: 0,
        },
        // Lists
        "& ul, & ol": {
          pl: 2.5,
          my: 0.75,
        },
        "& li": {
          mb: 0.5,
          lineHeight: 1.7,
          "& p": { mb: 0.25 },
        },
        "& li::marker": {
          color: "text.disabled",
        },
        // Links
        "& a": {
          color: "primary.main",
          textDecoration: "none",
          fontWeight: 500,
          "&:hover": {
            textDecoration: "underline",
          },
        },
        // Blockquotes
        "& blockquote": {
          borderLeft: 3,
          borderColor: "border.default",
          pl: 2,
          ml: 0,
          my: 1.5,
          color: "text.secondary",
          fontStyle: "italic",
        },
        // Horizontal rules
        "& hr": {
          border: "none",
          borderTop: 1,
          borderColor: "border.default",
          my: 2,
        },
        // Strong / em
        "& strong": {
          fontWeight: 600,
          color: "text.primary",
        },
      }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ..._ }) {
            // In react-markdown v9, code inside <pre> has a className
            // while inline code does not
            const isBlock = Boolean(className);
            return (
              <CodeBlockWrapper className={isBlock ? className : undefined}>
                {children}
              </CodeBlockWrapper>
            );
          },
          pre({ children }) {
            // Let CodeBlockWrapper handle the pre styling
            return <>{children}</>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </Box>
  );
}

TextBlock.propTypes = {
  content: PropTypes.string,
};
