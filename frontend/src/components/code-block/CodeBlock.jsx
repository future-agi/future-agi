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
// code's right padding grows to its width so no line ends beneath it.
// Unwrapped code scrolls sideways. Padding alone doesn't stop a long line
// painting under the control (overflow shows through the padding box), so
// while more of a line lies to the right, the code fades out over 24px just
// before the reserved width and stays hidden beneath the control.
const EDGE = 6;
const GAP = 8;
const FADE = 24;
const PAD = 12;
const LINE = 18;

// Two mask layers (alpha only): in the band beside the control, code fades
// out over FADE px and stays hidden beneath it; below the band, lines fade
// over the last FADE px of the block. Either way the fade says "scroll for more".
function fadeMask(reserve, band) {
  const beside = `linear-gradient(to right, black calc(100% - ${reserve + FADE}px), transparent calc(100% - ${reserve}px))`;
  const below = `linear-gradient(to right, black calc(100% - ${FADE}px), transparent)`;
  const layers = {
    image: `${beside}, ${below}`,
    size: `100% ${band}px, 100% calc(100% - ${band}px)`,
    position: "left top, left bottom",
    repeat: "no-repeat",
  };
  return {
    WebkitMaskImage: layers.image,
    maskImage: layers.image,
    WebkitMaskSize: layers.size,
    maskSize: layers.size,
    WebkitMaskPosition: layers.position,
    maskPosition: layers.position,
    WebkitMaskRepeat: layers.repeat,
    maskRepeat: layers.repeat,
  };
}

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
  const [more, setMore] = useState(false);
  const controlRef = useRef(null);
  const preRef = useRef(null);
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
  // Measure the control (width to reserve, height of the band it covers) so
  // the code never runs under it.
  useEffect(() => {
    const node = controlRef.current;
    if (!node) return undefined;
    const measure = () => {
      const box = node.getBoundingClientRect();
      // The band ends on a line boundary (12px padding, 18px lines), so no line is half hidden.
      const lines = Math.ceil(Math.max(0, box.height + EDGE - PAD) / LINE);
      const next = { width: Math.ceil(box.width) + EDGE + GAP, band: PAD + lines * LINE };
      setReserve((current) => (current && current.width === next.width && current.band === next.band ? current : next));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [floating]);
  // Track whether more of a line lies to the right (unwrapped code only).
  useEffect(() => {
    const pre = preRef.current;
    if (wrap || !pre) return undefined;
    const check = () => setMore(pre.scrollLeft + pre.clientWidth < pre.scrollWidth - 1);
    check();
    pre.addEventListener("scroll", check, { passive: true });
    let observer;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(check);
      observer.observe(pre);
    }
    return () => {
      pre.removeEventListener("scroll", check);
      observer?.disconnect();
    };
  }, [wrap, code, reserve]);

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
        ref={preRef}
        aria-label={label}
        tabIndex={0}
        data-fade-end={wrap ? undefined : String(more)}
        sx={{
          m: 0,
          p: 1.5,
          // The default icon keeps its 40px; a floating button reserves its measured width.
          pr: floating ? (reserve?.width ? `${reserve.width}px` : 11) : 5,
          ...(!wrap && more && fadeMask(floating ? reserve?.width || 88 : 40, reserve?.band || PAD + 2 * LINE)),
          fontFamily: "monospace",
          fontSize: 12,
          lineHeight: `${LINE}px`,
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
