import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import { GATEWAY_MONO } from "../../utils/fonts";

// A read-only JSON (or text) block on the neutral surface.
export default function JsonBlock({ value, maxHeight = 320, label }) {
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <Box
      component="pre"
      aria-label={label}
      sx={{
        m: 0,
        p: 1.5,
        maxHeight,
        overflow: "auto",
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        color: "text.primary",
        fontFamily: GATEWAY_MONO,
        fontSize: 12,
        lineHeight: "18px",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {text}
    </Box>
  );
}

JsonBlock.propTypes = {
  value: PropTypes.any,
  maxHeight: PropTypes.number,
  label: PropTypes.string,
};
