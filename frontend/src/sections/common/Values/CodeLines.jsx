import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { MONO } from "./CodeBlock";
import { toCodeLines, wordsOf } from "./jsonLines";

const TOKEN_COLOR = {
  key: "text.primary",
  string: "syntax.string",
  number: "syntax.number",
  boolean: "syntax.boolean",
  null: "text.disabled",
  punct: "text.secondary",
};

// A word never breaks inside itself (`A-4411` stays whole); the spaces
// between words are the only wrap points.
const Words = ({ text }) =>
  wordsOf(text).map((w, i) =>
    /^ +$/.test(w) ? (
      w
    ) : (
      <Box key={i} component="span" sx={{ whiteSpace: "nowrap" }}>
        {w}
      </Box>
    ),
  );

/**
 * CodeLines — monospace text with a line-number gutter and JSON syntax
 * colours (the Row view's Raw tab). `wrap` off scrolls sideways; on, lines
 * wrap at spaces only. Line numbers never get selected with the code.
 *
 * @param {string} props.text
 * @param {boolean} [props.json=true]  colour JSON tokens
 * @param {boolean} [props.wrap=false]
 * @param {number} [props.maxHeight]
 * @param {number} [props.startAt=1]
 */
const CodeLines = ({
  text,
  json = true,
  wrap = false,
  maxHeight,
  startAt = 1,
}) => {
  const lines = useMemo(() => toCodeLines(text, { json }), [text, json]);
  const gutter = String(lines.length + startAt - 1).length;
  return (
    <Box
      data-testid="code-lines"
      sx={{
        borderRadius: 0.5,
        bgcolor: "background.neutral",
        overflowX: "auto",
        overflowY: maxHeight ? "auto" : "visible",
        maxHeight: maxHeight || undefined,
        py: 0.75,
      }}
    >
      <Box
        component="div"
        role="presentation"
        sx={{
          display: "grid",
          gridTemplateColumns: `${gutter + 2}ch minmax(0, 1fr)`,
          width: wrap ? "100%" : "max-content",
          minWidth: "100%",
          typography: "s2",
          fontFamily: MONO,
          lineHeight: "20px",
        }}
      >
        {lines.map((l) => (
          <React.Fragment key={l.n}>
            <Box
              aria-hidden
              sx={{
                pr: 1,
                textAlign: "right",
                color: "text.disabled",
                userSelect: "none",
                fontVariantNumeric: "tabular-nums",
                position: wrap ? "static" : "sticky",
                left: 0,
                bgcolor: "background.neutral",
              }}
            >
              {l.n + startAt - 1}
            </Box>
            <Box
              component="span"
              sx={{
                pr: 1.5,
                whiteSpace: wrap ? "pre-wrap" : "pre",
                wordBreak: "normal",
                overflowWrap: wrap ? "anywhere" : "normal",
                minWidth: 0,
              }}
            >
              {l.tokens.length === 0
                ? "​"
                : l.tokens.map((tk, i) => (
                    <Box
                      key={i}
                      component="span"
                      sx={{ color: TOKEN_COLOR[tk.t] ?? "text.primary" }}
                    >
                      {wrap && tk.t === "string" ? (
                        <Words text={tk.text} />
                      ) : (
                        tk.text
                      )}
                    </Box>
                  ))}
            </Box>
          </React.Fragment>
        ))}
      </Box>
    </Box>
  );
};

CodeLines.propTypes = {
  text: PropTypes.string.isRequired,
  json: PropTypes.bool,
  wrap: PropTypes.bool,
  maxHeight: PropTypes.number,
  startAt: PropTypes.number,
};

export default CodeLines;
