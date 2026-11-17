import React from "react";
import PropTypes from "prop-types";
import SvgIcon from "@mui/material/SvgIcon";

// Outline icons for the Studio tab on a 24 px grid with a 1.5 px stroke, the
// same weight as the Omega shell's icons. Inline, so the page fetches nothing
// to draw them.
const GLYPHS = {
  studio: (
    <>
      <rect width="8" height="8" x="3" y="3" rx="2" />
      <path d="M7 11v4a2 2 0 0 0 2 2h4" />
      <rect width="8" height="8" x="13" y="13" rx="2" />
    </>
  ),
  external: (
    <>
      <path d="M15 3h6v6" />
      <path d="M10 14 21 3" />
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    </>
  ),
  copy: (
    <>
      <rect width="14" height="14" x="8" y="8" rx="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </>
  ),
  check: <path d="M20 6 9 17l-5-5" />,
  pencil: (
    <>
      <path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z" />
      <path d="m15 5 4 4" />
    </>
  ),
  trash: (
    <>
      <path d="M3 6h18" />
      <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
      <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
    </>
  ),
  terminal: (
    <>
      <path d="m4 17 6-6-6-6" />
      <path d="M12 19h8" />
    </>
  ),
  plug: (
    <>
      <path d="M12 22v-5" />
      <path d="M9 8V2" />
      <path d="M15 8V2" />
      <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />
    </>
  ),
};

export const STUDIO_ICON_NAMES = Object.keys(GLYPHS);

export default function StudioIcon({ name, size = 18, sx, ...other }) {
  return (
    <SvgIcon
      viewBox="0 0 24 24"
      data-studio-icon={name}
      sx={{
        width: size,
        height: size,
        fontSize: size,
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
      {GLYPHS[name]}
    </SvgIcon>
  );
}

StudioIcon.propTypes = {
  name: PropTypes.oneOf(STUDIO_ICON_NAMES).isRequired,
  size: PropTypes.number,
  sx: PropTypes.object,
};
