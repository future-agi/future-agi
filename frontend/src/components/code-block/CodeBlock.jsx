import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

// Design System code block: monospace 12/18 on bg.muted with a hairline,
// a copy control floating top-right. `wrap` for prose (prompts), off for code.
// `copyText` copies something other than what's shown — a snippet that
// displays keys masked copies the real ones.
// The control is one of: a caller's `action` node, a small outlined "Copy"
// button (`copyLabel`, with `ariaLabel` keeping a caller's accessible name),
// or the default icon button. Every one floats inside the block, and the
// code's right padding grows to its width so no line hides beneath it.
const EDGE = 6;
const GAP = 8;

export default function CodeBlock({
  code,
  wrap = false,
  onCopy,
  copyText,
  action,
  label,
  copyLabel,
  ariaLabel,
}) {
  const [copied, setCopied] = useState(false);
  const [reserve, setReserve] = useState(null);
  const controlRef = useRef(null);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = () => {
    navigator.clipboard?.writeText(copyText ?? code);
    onCopy?.();
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1500);
  };
  const floating = Boolean(action || copyLabel);
  // Measure the floating control so the code never runs under it.
  useEffect(() => {
    const node = controlRef.current;
    if (!floating || !node) return undefined;
    const measure = () => setReserve(Math.ceil(node.getBoundingClientRect().width) + EDGE + GAP);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [floating]);

  let control;
  if (action) control = action;
  else if (copyLabel)
    control = (
      <Button
        variant="outlined"
        size="small"
        onClick={copy}
        aria-label={ariaLabel}
        startIcon={
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={16}
          />
        }
        sx={{ bgcolor: "background.paper" }}
      >
        {copied ? "Copied" : copyLabel}
      </Button>
    );
  else
    control = (
      <Tooltip title={copied ? "Copied" : "Copy"}>
        <IconButton size="small" onClick={copy} aria-label={ariaLabel || "Copy code"}>
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={16}
          />
        </IconButton>
      </Tooltip>
    );

  return (
    <Box
      sx={{
        position: "relative",
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.muted",
      }}
    >
      <Box
        ref={controlRef}
        sx={{
          position: "absolute",
          top: EDGE,
          right: EDGE,
          zIndex: 1,
          display: "flex",
          alignItems: "center",
          gap: 1,
        }}
      >
        {control}
      </Box>
      <Box
        component="pre"
        aria-label={label}
        tabIndex={0}
        sx={{
          m: 0,
          p: 1.5,
          // The default icon keeps its 40px; a floating button reserves its measured width.
          pr: floating ? (reserve ? `${reserve}px` : 12) : 5,
          fontFamily: "monospace",
          fontSize: 12,
          lineHeight: "18px",
          color: "text.primary",
          overflowX: "auto",
          whiteSpace: wrap ? "pre-wrap" : "pre",
        }}
      >
        {code}
      </Box>
    </Box>
  );
}

CodeBlock.propTypes = {
  action: PropTypes.node,
  label: PropTypes.string,
  code: PropTypes.string.isRequired,
  wrap: PropTypes.bool,
  onCopy: PropTypes.func,
  copyText: PropTypes.string,
  copyLabel: PropTypes.string,
  ariaLabel: PropTypes.string,
};
