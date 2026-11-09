import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button } from "@mui/material";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";
const TOKEN =
  /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*")(\s*:)?|\b(true|false)\b|\b(null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
const MAX_HIGHLIGHT = 120000;
const PREVIEW_CHARS = 20000;

/** JSON text → spans coloured with the theme's syntax tokens. */
function highlightJson(text) {
  if (text.length > MAX_HIGHLIGHT) return text;
  const out = [];
  let last = 0;
  let i = 0;
  text.replace(TOKEN, (match, str, colon, bool, nul, num, offset) => {
    if (offset > last) out.push(text.slice(last, offset));
    const key = i++;
    if (str && colon)
      out.push(
        <Box key={key} component="span" sx={{ color: "text.primary" }}>
          {str}
        </Box>,
        colon,
      );
    else if (str)
      out.push(
        <Box key={key} component="span" sx={{ color: "syntax.string" }}>
          {str}
        </Box>,
      );
    else if (bool)
      out.push(
        <Box key={key} component="span" sx={{ color: "syntax.boolean" }}>
          {bool}
        </Box>,
      );
    else if (nul)
      out.push(
        <Box key={key} component="span" sx={{ color: "text.disabled" }}>
          {nul}
        </Box>,
      );
    else
      out.push(
        <Box key={key} component="span" sx={{ color: "syntax.number" }}>
          {num}
        </Box>,
      );
    last = offset + match.length;
    return match;
  });
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/**
 * A monospace block for the JSON / YAML / Text / Raw modes: wraps or
 * scrolls, clamps very long text with `Show all (n chars)`, and colours
 * JSON tokens.
 */
const CodeBlock = ({
  text,
  language = "text",
  wrap = true,
  maxHeight = 480,
  bare = false,
}) => {
  const [all, setAll] = useState(false);
  const long = text.length > PREVIEW_CHARS;
  const shown = long && !all ? `${text.slice(0, PREVIEW_CHARS)}…` : text;
  const content = useMemo(
    () => (language === "json" ? highlightJson(shown) : shown),
    [shown, language],
  );
  return (
    <Box>
      <Box
        component="pre"
        sx={{
          m: 0,
          ...(bare
            ? {}
            : { p: 1.5, borderRadius: 0.5, bgcolor: "background.neutral" }),
          typography: "s2",
          fontFamily: MONO,
          whiteSpace: wrap ? "pre-wrap" : "pre",
          wordBreak: wrap ? "break-word" : "normal",
          overflowX: wrap ? "hidden" : "auto",
          color: "text.primary",
          maxHeight: maxHeight || undefined,
          overflowY: maxHeight ? "auto" : "visible",
        }}
      >
        {content}
      </Box>
      {long && (
        <Button
          size="small"
          variant="text"
          onClick={() => setAll((a) => !a)}
          sx={{ px: 0, mt: 0.5, minWidth: 0, typography: "s2" }}
        >
          {all
            ? "Show less"
            : `Show all (${text.length.toLocaleString("en-US")} chars)`}
        </Button>
      )}
    </Box>
  );
};

CodeBlock.propTypes = {
  text: PropTypes.string.isRequired,
  language: PropTypes.oneOf(["json", "yaml", "text"]),
  wrap: PropTypes.bool,
  maxHeight: PropTypes.number,
  bare: PropTypes.bool,
};

export default CodeBlock;
export { MONO };
