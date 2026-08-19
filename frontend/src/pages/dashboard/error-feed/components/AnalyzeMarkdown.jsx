import { Box, alpha } from "@mui/material";
import PropTypes from "prop-types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { purple } from "src/theme/palette";

const ACCENT = purple[500];

// Compact markdown renderer for agent prose (reasoning, synthesis). The model
// emits real markdown — bold, `code`, headings, lists — so render it instead of
// dumping the raw `**`/backtick syntax. Styling inherits the caller's font via
// sx; element margins are tightened to chat density.
function AnalyzeMarkdown({
  text,
  fontSize = "12px",
  color = "text.secondary",
  italic = false,
  sx,
}) {
  return (
    <Box
      sx={{
        fontSize,
        color,
        lineHeight: 1.6,
        fontStyle: italic ? "italic" : "normal",
        wordBreak: "break-word",
        "& > :first-of-type": { mt: 0 },
        "& > :last-child": { mb: 0 },
        "& p": { m: 0, mb: 0.75 },
        "& strong": { fontWeight: 700, color: "text.primary" },
        "& em": { fontStyle: "italic" },
        "& a": { color: ACCENT, textDecoration: "underline" },
        "& ul, & ol": { m: 0, mb: 0.75, pl: 2.25 },
        "& li": { mb: 0.2 },
        "& h1, & h2, & h3, & h4, & h5, & h6": {
          fontSize: "1.05em",
          fontWeight: 700,
          color: "text.primary",
          m: 0,
          mb: 0.4,
        },
        "& code": {
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          fontSize: "0.9em",
          fontStyle: "normal",
          px: 0.5,
          py: "1px",
          borderRadius: "3px",
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? alpha("#fff", 0.08)
              : alpha("#000", 0.05),
        },
        "& pre": {
          m: 0,
          mb: 0.75,
          p: 1,
          borderRadius: "6px",
          overflowX: "auto",
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? alpha("#fff", 0.05)
              : alpha("#000", 0.04),
        },
        "& pre code": { bgcolor: "transparent", p: 0, fontSize: "11px" },
        "& blockquote": {
          m: 0,
          mb: 0.75,
          pl: 1,
          borderLeft: "2px solid",
          borderColor: "divider",
          color: "text.secondary",
        },
        "& table": {
          borderCollapse: "collapse",
          width: "auto",
          my: 0.75,
          fontSize: "0.95em",
          display: "block",
          overflowX: "auto",
        },
        "& th, & td": {
          border: "1px solid",
          borderColor: "divider",
          px: 1,
          py: 0.5,
          textAlign: "left",
        },
        "& th": {
          fontWeight: 700,
          color: "text.primary",
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? alpha("#fff", 0.04)
              : alpha("#000", 0.03),
        },
        "& hr": {
          border: "none",
          borderTop: "1px solid",
          borderColor: "divider",
          my: 1,
        },
        ...sx,
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text || ""}</ReactMarkdown>
    </Box>
  );
}
AnalyzeMarkdown.propTypes = {
  text: PropTypes.string,
  fontSize: PropTypes.string,
  color: PropTypes.string,
  italic: PropTypes.bool,
  sx: PropTypes.object,
};

export default AnalyzeMarkdown;
