import React, { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const TOOLTIP_CHARS = 600;
const words = (s) =>
  String(s ?? "").trim() ? String(s).trim().split(/\s+/).length : 0;
const looksJson = (s) => /^\s*[[{]/.test(String(s ?? ""));

/** Single-line text cut at a word boundary to fit `chars` (never mid-word). */
export function cutAtWord(text, chars) {
  const s = String(text ?? "");
  if (s.length <= chars) return s;
  const head = s.slice(0, chars);
  const space = head.lastIndexOf(" ");
  return `${(space > chars * 0.5 ? head.slice(0, space) : head).trimEnd()}…`;
}

/**
 * Truncate — clamp text to `lines` with a 24 px fade; full text (first 600
 * chars, mono for JSON) in a tooltip after 400 ms when it is cut;
 * `expandable` adds `Show more (+143 words)` / `Show less`.
 *
 * Props (frozen):
 * @param {string} props.text
 * @param {number} [props.lines=1]
 * @param {boolean} [props.expandable]
 * @param {boolean} [props.mono]  monospace (auto for JSON)
 * @param {object} [props.sx]
 */
export default function Truncate({
  text,
  lines = 1,
  expandable = false,
  mono,
  sx,
}) {
  const ref = useRef(null);
  const measureRef = useRef(null);
  const [cut, setCut] = useState(false);
  const [fit, setFit] = useState(null); // single line: chars that fit
  const [open, setOpen] = useState(false);
  const value = String(text ?? "");
  const isMono = mono ?? looksJson(value);
  const single = lines === 1 && !open;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => {
      if (single && measureRef.current) {
        const full = measureRef.current.getBoundingClientRect().width;
        const box = el.getBoundingClientRect().width;
        const over = full > box + 1;
        setCut(over);
        setFit(
          over
            ? Math.max(1, Math.floor((value.length * (box - 14)) / full))
            : null,
        );
        return;
      }
      setFit(null);
      setCut(
        el.scrollHeight > el.clientHeight + 1 ||
          el.scrollWidth > el.clientWidth + 1,
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
    // `cut` re-wraps the body in a tooltip (a new element): observe that one,
    // not the detached first one, or an early narrow measure sticks ("#…").
  }, [value, lines, open, single, cut]);

  // Single line: never cut mid-word — the ellipsis sits at a space.
  const shown = single && fit != null ? cutAtWord(value, fit) : value;
  const clampSx = open
    ? {}
    : lines === 1
      ? { whiteSpace: "nowrap", overflow: "hidden", position: "relative" }
      : {
          display: "-webkit-box",
          WebkitBoxOrient: "vertical",
          WebkitLineClamp: lines,
          overflow: "hidden",
        };
  const fade =
    !open && cut && lines > 1
      ? {
          position: "relative",
          "&::after": {
            content: '""',
            position: "absolute",
            right: 0,
            bottom: 0,
            width: 24,
            height: "1.5em",
            background: (theme) =>
              `linear-gradient(to right, transparent, ${theme.palette.background.paper})`,
            pointerEvents: "none",
          },
        }
      : {};
  const body = (
    <Box
      ref={ref}
      component="span"
      data-qa-truncate=""
      sx={{
        display: lines === 1 && !open ? "block" : undefined,
        minWidth: 0,
        wordBreak: "break-word",
        fontFamily: isMono ? "monospace" : undefined,
        ...clampSx,
        ...fade,
        ...sx,
      }}
    >
      {shown}
      {single && (
        <Box
          component="span"
          ref={measureRef}
          aria-hidden
          sx={{
            position: "absolute",
            visibility: "hidden",
            whiteSpace: "nowrap",
            left: 0,
            top: 0,
            pointerEvents: "none",
          }}
        >
          {value}
        </Box>
      )}
    </Box>
  );
  const shownWords = (() => {
    if (!cut) return 0;
    if (single) return Math.max(0, words(value) - words(shown));
    const el = ref.current;
    if (!el) return 0;
    const ratio = el.clientHeight / Math.max(1, el.scrollHeight);
    return Math.max(0, Math.round(words(value) * (1 - ratio)));
  })();
  return (
    <Box sx={{ minWidth: 0 }}>
      {cut && !open ? (
        <CustomTooltip
          show
          size="small"
          enterDelay={400}
          placement="top"
          title={
            <Box
              component="span"
              sx={{
                whiteSpace: "pre-wrap",
                fontFamily: isMono ? "monospace" : undefined,
              }}
            >
              {value.length > TOOLTIP_CHARS
                ? `${value.slice(0, TOOLTIP_CHARS)}…`
                : value}
            </Box>
          }
        >
          {body}
        </CustomTooltip>
      ) : (
        body
      )}
      {expandable && (cut || open) && (
        <Button
          size="small"
          variant="text"
          onClick={(e) => {
            e.stopPropagation();
            setOpen((o) => !o);
          }}
          sx={{ typography: "s3", px: 0.5, minWidth: 0, mt: 0.25 }}
        >
          {open
            ? "Show less"
            : `Show more${shownWords ? ` (+${shownWords} words)` : ""}`}
        </Button>
      )}
    </Box>
  );
}

Truncate.propTypes = {
  text: PropTypes.string,
  lines: PropTypes.number,
  expandable: PropTypes.bool,
  mono: PropTypes.bool,
  sx: PropTypes.object,
};
