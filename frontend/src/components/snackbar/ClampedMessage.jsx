import React, { useLayoutEffect, useRef, useState } from "react";
import { Box } from "@mui/material";
import PropTypes from "prop-types";

const MAX_LINES = 3;

export default function ClampedMessage({ children }) {
  const textRef = useRef(null);
  const [overflowing, setOverflowing] = useState(false);
  const [expanded, setExpanded] = useState(false);

  // Measure once against the collapsed (clamped) height; a toast's message
  // never changes over its lifetime, so this stays correct across toggles.
  useLayoutEffect(() => {
    const el = textRef.current;
    if (el) setOverflowing(el.scrollHeight > el.clientHeight + 1);
  }, [children]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
      <Box
        ref={textRef}
        sx={{
          wordBreak: "break-word",
          ...(expanded
            ? {}
            : {
                display: "-webkit-box",
                WebkitBoxOrient: "vertical",
                WebkitLineClamp: MAX_LINES,
                overflow: "hidden",
              }),
        }}
      >
        {children}
      </Box>
      {overflowing && (
        <Box
          component="button"
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((prev) => !prev)}
          sx={{
            alignSelf: "flex-start",
            p: 0,
            border: "none",
            background: "none",
            cursor: "pointer",
            font: "inherit",
            fontWeight: 600,
            color: "text.secondary",
            textDecoration: "underline",
            "&:hover": { color: "text.primary" },
          }}
        >
          {expanded ? "Show less" : "Show more"}
        </Box>
      )}
    </Box>
  );
}

ClampedMessage.propTypes = {
  children: PropTypes.node,
};
