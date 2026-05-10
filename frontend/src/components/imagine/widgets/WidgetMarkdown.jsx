import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { useTheme } from "@mui/material/styles";

// Escape HTML so that user-/LLM-supplied content cannot inject raw markup
// (script tags, event handlers, javascript: hrefs) when rendered via
// dangerouslySetInnerHTML below. Markdown substitutions re-introduce a small
// allow-list of tags AFTER escaping.
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export default function WidgetMarkdown({ config }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const content = config.content || "";

  const codeBg = isDark ? "#2d2d2d" : "#f5f5f5";
  const inlineBg = isDark ? "#3a3a3a" : "#f0f0f0";

  const html = escapeHtml(content)
    .replace(
      /```(\w*)\n([\s\S]*?)```/g,
      `<pre style="background:${codeBg};padding:8px;border-radius:4px;overflow-x:auto;font-size:12px;"><code>$2</code></pre>`,
    )
    .replace(
      /`([^`]+)`/g,
      `<code style="background:${inlineBg};padding:1px 4px;border-radius:3px;font-size:12px;">$1</code>`,
    )
    // Headers
    .replace(
      /^### (.+)$/gm,
      '<h4 style="margin:8px 0 4px;font-size:13px;font-weight:600;">$1</h4>',
    )
    .replace(
      /^## (.+)$/gm,
      '<h3 style="margin:10px 0 4px;font-size:14px;font-weight:600;">$1</h3>',
    )
    .replace(
      /^# (.+)$/gm,
      '<h2 style="margin:12px 0 6px;font-size:16px;font-weight:700;">$1</h2>',
    )
    // Bold & italic
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    // Unordered lists
    .replace(
      /^- (.+)$/gm,
      '<li style="margin-left:16px;font-size:13px;">$1</li>',
    )
    // Line breaks
    .replace(/\n\n/g, "<br/><br/>")
    .replace(/\n/g, "<br/>");

  return (
    <Box
      sx={{
        height: "100%",
        overflow: "auto",
        px: 1.5,
        py: 1,
        fontSize: 13,
        lineHeight: 1.6,
        color: "text.primary",
        "& pre": { bgcolor: "action.hover" },
        "& code": { fontFamily: "'IBM Plex Mono', monospace" },
        "& strong": { fontWeight: 600 },
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

WidgetMarkdown.propTypes = { config: PropTypes.object.isRequired };
