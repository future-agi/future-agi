/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

// Inline 24 px outline glyphs (1.5 px stroke, currentColor), so the tab never
// fetches an icon from a CDN and every icon follows the text colour.
const GLYPHS = {
  box: "M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z M3.3 7l8.7 5 8.7-5 M12 22V12",
  plus: "M12 5v14 M5 12h14",
  chevronLeft: "m15 18-6-6 6-6",
  chevronRight: "m9 18 6-6-6-6",
  close: "M18 6 6 18 M6 6l12 12",
  folder:
    "M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z",
  file: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z M14 2v4a2 2 0 0 0 2 2h4",
  play: "M6 3l14 9-14 9V3z",
  stop: "M6 6h12v12H6z",
  pause: "M10 4H6v16h4z M18 4h-4v16h4z",
  camera:
    "M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  restore: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8 M3 3v5h5",
  globe:
    "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M2 12h20 M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z",
  shield:
    "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z",
  terminal: "m4 17 6-6-6-6 M12 19h8",
  activity: "M22 12h-4l-3 9L9 3l-3 9H2",
  trash:
    "M3 6h18 M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6 M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2",
  copy: "M8 8h12v12H8z M4 16V4h12",
  check: "M20 6 9 17l-5-5",
  cloudOff:
    "m2 2 20 20 M5.78 5.78A7 7 0 0 0 9 19h8.5a4.5 4.5 0 0 0 1.31-.2 M21.53 15.38A4.5 4.5 0 0 0 17.5 10h-1.79A7 7 0 0 0 9.5 5.06",
  lock: "M5 11h14v10H5z M8 11V7a4 4 0 0 1 8 0v4",
  refresh:
    "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16 M3 21v-5h5 M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8 M21 3v5h-5",
  edit: "M12 20h9 M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z",
  download: "M12 3v12 M7 10l5 5 5-5 M5 21h14",
  upload: "M12 21V9 M7 14l5-5 5 5 M5 3h14",
  more: "M12 6h.01 M12 12h.01 M12 18h.01",
  folderPlus:
    "M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z M12 10v6 M9 13h6",
};

export const SANDBOX_ICON_NAMES = Object.keys(GLYPHS);

export default function SandboxIcon({ name, size = 18, sx, ...other }) {
  return (
    <Box
      component="svg"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      data-sandbox-icon={name}
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

SandboxIcon.propTypes = {
  name: PropTypes.oneOf(SANDBOX_ICON_NAMES).isRequired,
  size: PropTypes.number,
  sx: PropTypes.object,
};
