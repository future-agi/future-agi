/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

// Inline 24 px outline glyphs (1.5 px stroke, currentColor), as the Sandboxes
// and Deployments tabs draw theirs, so the panel fetches no icon font.
const GLYPHS = {
  code: "m16 18 6-6-6-6 M8 6l-6 6 6 6",
  copy: "M8 8h12v12H8z M4 16V4h12",
  check: "M20 6 9 17l-5-5",
  download: "M12 3v12 M7 10l5 5 5-5 M5 21h14",
  key: "M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  eyeOff:
    "M9.88 9.88a3 3 0 1 0 4.24 4.24 M10.73 5.08A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a13.2 13.2 0 0 1-1.67 2.68 M6.61 6.61A13.5 13.5 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.39-1.61 M2 2l20 20",
  close: "M18 6 6 18 M6 6l12 12",
  alert:
    "M12 9v4 M12 17h.01 M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z",
};

export const CODE_ICON_NAMES = Object.keys(GLYPHS);

export default function CodeIcon({ name, size = 16, sx, ...other }) {
  return (
    <Box
      component="svg"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      data-code-icon={name}
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 1.5,
        strokeLinecap: "round",
        strokeLinejoin: "round",
        ...sx,
      }}
      {...other}
    >
      <path d={GLYPHS[name]} />
    </Box>
  );
}

CodeIcon.propTypes = {
  name: PropTypes.oneOf(CODE_ICON_NAMES).isRequired,
  size: PropTypes.number,
  sx: PropTypes.object,
};
