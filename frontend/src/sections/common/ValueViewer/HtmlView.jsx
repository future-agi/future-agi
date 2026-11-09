import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { useTheme } from "@mui/material/styles";

// W3-2 B4 (D22): what the frame's document may load. Nothing from the
// network, inline styles only, images only as data: URLs; scripts are off
// twice over (the empty sandbox and `default-src 'none'`).
export const HTML_VIEW_CSP =
  "default-src 'none'; img-src data:; style-src 'unsafe-inline'";

const escapeAttr = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");

/** The frame's srcdoc: the value inside a CSP-locked page. Pure. */
export function htmlViewDocument(html, { color, background, font } = {}) {
  const style = [
    `body{margin:8px;font:14px/1.5 ${font ?? "system-ui, sans-serif"};`,
    color ? `color:${color};` : "",
    background ? `background:${background};` : "",
    "overflow-wrap:anywhere}img{max-width:100%}table{border-collapse:collapse}td,th{border:1px solid #8884;padding:2px 6px}",
  ].join("");
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${escapeAttr(HTML_VIEW_CSP)}"><style>${style}</style></head><body>${String(html ?? "")}</body></html>`;
}

/**
 * HtmlView — HTML text rendered in `<iframe sandbox="" srcdoc>`: no
 * scripts, no forms, no popups, no network, its own opaque origin.
 */
const HtmlView = ({ html, height = 240 }) => {
  const theme = useTheme();
  const doc = useMemo(
    () =>
      htmlViewDocument(html, {
        color: theme.palette.text.primary,
        background: theme.palette.background.paper,
        font: theme.typography.fontFamily,
      }),
    [html, theme],
  );
  return (
    <Box
      component="iframe"
      title="HTML view"
      sandbox=""
      srcDoc={doc}
      referrerPolicy="no-referrer"
      data-testid="value-html-frame"
      sx={{
        display: "block",
        width: "100%",
        height,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        resize: "vertical",
        overflow: "auto",
      }}
    />
  );
};

HtmlView.propTypes = {
  html: PropTypes.string,
  height: PropTypes.number,
};

export default HtmlView;
