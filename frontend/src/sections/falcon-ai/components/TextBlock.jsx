import React, { useState, useCallback } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { alpha, useTheme } from "@mui/material/styles";
import { useRouter } from "src/routes/hooks";
import Iconify from "src/components/iconify";

// Links in assistant markdown: internal deep links (/dashboard/...) navigate
// client-side (no full reload, SPA state preserved); external links open in a
// new tab so the user never loses the conversation.
function MarkdownLink({ href, children, router }) {
  if (href && href.startsWith("/")) {
    return (
      <a
        href={href}
        onClick={(e) => {
          e.preventDefault();
          router.push(href);
        }}
      >
        {children}
      </a>
    );
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

MarkdownLink.propTypes = {
  href: PropTypes.string,
  children: PropTypes.node,
  router: PropTypes.object.isRequired,
};

function CodeBlockWrapper({ children, className }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
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
          bgcolor: isDark
            ? alpha(theme.palette.common.white, 0.08)
            : alpha(theme.palette.common.black, 0.06),
          color: isDark
            ? theme.palette.primary.light || "#A792FD"
            : theme.palette.primary.dark || "#5A41BD",
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
    <Box sx={{ position: "relative", my: 1.5 }}>
      {/* Header bar */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 1.5,
          py: 0.75,
          bgcolor: isDark ? "#1e1e1e" : "#f6f6f6",
          borderTopLeftRadius: "10px",
          borderTopRightRadius: "10px",
          borderBottom: 1,
          borderColor: isDark
            ? alpha(theme.palette.common.white, 0.06)
            : alpha(theme.palette.common.black, 0.06),
        }}
      >
        <Box
          component="span"
          sx={{
            fontSize: 11,
            fontFamily: "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
            color: "text.disabled",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          {language}
        </Box>
        <IconButton
          size="small"
          onClick={handleCopy}
          sx={{
            p: 0.25,
            color: "text.disabled",
            "&:hover": { color: "text.secondary" },
          }}
          title={copied ? "Copied!" : "Copy code"}
        >
          <Iconify
            icon={copied ? "mdi:check" : "mdi:content-copy"}
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
          bgcolor: isDark ? "#161616" : "#fafafa",
          color: isDark ? "#e4e4e7" : "#1a1a1a",
          borderBottomLeftRadius: "10px",
          borderBottomRightRadius: "10px",
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
  const theme = useTheme();
  const router = useRouter();
  const isDark = theme.palette.mode === "dark";

  if (!content) return null;

  return (
    <Box
      sx={{
        fontSize: 14,
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
        "& h2": { fontSize: 17 },
        "& h3": { fontSize: 15 },
        // Tables — width: max-content + the scroll wrapper (components.table
        // below) lets wide tables scroll horizontally instead of crushing
        // columns into unreadable slivers; minWidth keeps small tables
        // full-width. Cell maxWidth still wraps very long text.
        "& table": {
          borderCollapse: "collapse",
          minWidth: "100%",
          width: "max-content",
          my: 0,
          fontSize: 13,
          borderRadius: "8px",
          overflow: "hidden",
          border: 1,
          borderColor: isDark
            ? alpha(theme.palette.common.white, 0.08)
            : alpha(theme.palette.common.black, 0.08),
        },
        "& td, & th": {
          maxWidth: 360,
        },
        "& th": {
          bgcolor: isDark
            ? alpha(theme.palette.common.white, 0.06)
            : alpha(theme.palette.common.black, 0.03),
          fontWeight: 600,
          px: 1.5,
          py: 1,
          textAlign: "left",
          fontSize: 12,
          color: "text.secondary",
          borderBottom: 1,
          borderColor: isDark
            ? alpha(theme.palette.common.white, 0.08)
            : alpha(theme.palette.common.black, 0.08),
        },
        "& td": {
          px: 1.5,
          py: 0.75,
          borderBottom: 1,
          borderColor: isDark
            ? alpha(theme.palette.common.white, 0.04)
            : alpha(theme.palette.common.black, 0.04),
          fontSize: 13,
          color: "text.primary",
        },
        "& tr:last-child td": {
          borderBottom: 0,
        },
        "& tr:nth-of-type(even) td": {
          bgcolor: isDark
            ? alpha(theme.palette.common.white, 0.02)
            : alpha(theme.palette.common.black, 0.015),
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
          color: isDark
            ? theme.palette.primary.light || "#A792FD"
            : "primary.main",
          textDecoration: "none",
          fontWeight: 500,
          "&:hover": {
            textDecoration: "underline",
          },
        },
        // Blockquotes
        "& blockquote": {
          borderLeft: 3,
          borderColor: isDark
            ? alpha(theme.palette.common.white, 0.12)
            : alpha(theme.palette.primary.main, 0.3),
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
          borderColor: isDark
            ? alpha(theme.palette.common.white, 0.08)
            : alpha(theme.palette.common.black, 0.08),
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
          table({ children }) {
            // Horizontal scroll container — wide tables overflow the chat
            // column (800px full page, ~380px sidebar) instead of being
            // squeezed; data stays readable via scroll.
            return (
              <Box sx={{ overflowX: "auto", maxWidth: "100%", my: 1.5 }}>
                <table>{children}</table>
              </Box>
            );
          },
          a({ href, children }) {
            return (
              <MarkdownLink href={href} router={router}>
                {children}
              </MarkdownLink>
            );
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
