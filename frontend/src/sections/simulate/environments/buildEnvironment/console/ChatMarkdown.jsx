import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box } from "@mui/material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";

const MONO = "ui-monospace, Menlo, monospace";

// Raw HTML would be dropped by the sanitizer, losing words like "<front desk>".
const htmlAsText = () => (tree) => {
  const walk = (node) => {
    if (node.type === "html") node.type = "text";
    node.children?.forEach(walk);
  };
  walk(tree);
};

/** Markdown prose in the builder chat: builder notes and question prompts. */
export default function ChatMarkdown({ text, dim, sx }) {
  // Every keystroke in the composer redraws the chat; parse only when text changes.
  const body = useMemo(
    () => (
      <ReactMarkdown
        remarkPlugins={[remarkGfm, htmlAsText]}
        rehypePlugins={[rehypeSanitize]}
        // An image loads as soon as the chat opens, so model-written text could
        // send data to any site through its URL.
        disallowedElements={["img"]}
      >
        {text || ""}
      </ReactMarkdown>
    ),
    [text],
  );

  return (
    <Box
      sx={{
        typography: "m3",
        color: dim ? "text.secondary" : "text.primary",
        "& p": { m: 0, mb: 1 },
        "& p:last-of-type": { mb: 0 },
        "& h1, & h2, & h3, & h4, & h5, & h6": {
          font: "inherit",
          fontWeight: 600,
          m: 0,
          mt: 1.5,
          mb: 0.5,
        },
        "& ul, & ol": { m: 0, mb: 1, pl: 3 },
        "& code": { typography: "s1", fontFamily: MONO },
        "& a": { color: "text.primary", textDecoration: "underline" },
        // Code keeps its lines and tables keep whole words, so both scroll
        // sideways inside their own box rather than widening the chat.
        "& pre": {
          m: 0,
          mb: 1,
          px: 1.5,
          py: 1,
          borderRadius: "6px",
          bgcolor: "background.neutral",
          overflowX: "auto",
          maxWidth: "100%",
        },
        "& table": {
          display: "block",
          maxWidth: "100%",
          overflowX: "auto",
          borderCollapse: "collapse",
          mb: 1,
        },
        "& th, & td": {
          px: 1,
          py: 0.5,
          border: "1px solid",
          borderColor: "divider",
          textAlign: "left",
          overflowWrap: "normal",
        },
        ...sx,
      }}
    >
      {body}
    </Box>
  );
}
ChatMarkdown.propTypes = {
  text: PropTypes.string,
  dim: PropTypes.bool,
  sx: PropTypes.object,
};
