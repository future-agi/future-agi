import PropTypes from "prop-types";
import { Box } from "@mui/material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";

const MONO = "ui-monospace, Menlo, monospace";

export default function Markdown({ text, dim }) {
  return (
    <Box
      sx={{
        typography: "m3",
        color: dim ? "text.secondary" : "text.primary",
        "& p": { m: 0, mb: 1 },
        "& p:last-of-type": { mb: 0 },
        "& ul, & ol": { m: 0, mb: 1, pl: 3 },
        "& code": { fontFamily: MONO, fontSize: "0.85em" },
        "& a": { color: "text.primary", textDecoration: "underline" },
        // Code keeps its lines and tables keep whole words, so both scroll
        // sideways inside their own box rather than widening the chat.
        "& pre": {
          m: 0, mb: 1, px: 1.5, py: 1, borderRadius: "4px",
          bgcolor: "background.neutral", overflowX: "auto", maxWidth: "100%",
        },
        "& table": {
          display: "block", maxWidth: "100%", overflowX: "auto",
          borderCollapse: "collapse", mb: 1,
        },
        "& th, & td": {
          px: 1, py: 0.5, border: "1px solid", borderColor: "divider",
          textAlign: "left", overflowWrap: "normal",
        },
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
        {text || ""}
      </ReactMarkdown>
    </Box>
  );
}
Markdown.propTypes = { text: PropTypes.string, dim: PropTypes.bool };
