/* eslint-disable react-refresh/only-export-components */
import React from "react";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";

// Outline icons for the Omega shell, drawn on a 24 px grid with a 1.5 px
// stroke so they read at the same weight as the host's 20 px / 1.25 px nav
// icons. They are inlined as data URIs and painted through SvgColor's mask, so
// they follow the nav item colour (text.primary, or primary.main when active)
// exactly like the existing /assets/icons/navbar/*.svg icons, without a network
// fetch.

const GLYPHS = {
  studio:
    '<rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/>',
  deployments:
    '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
  sandboxes:
    '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  hub:
    '<rect width="7" height="7" x="14" y="3" rx="1"/><path d="M10 21V8a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5a1 1 0 0 0-1-1H3"/>',
  console:
    '<path d="M3 20h4.5a.5.5 0 0 0 .5-.5v-.28a.52.52 0 0 0-.25-.44 8 8 0 1 1 8.5 0 .52.52 0 0 0-.25.44v.28a.5.5 0 0 0 .5.5H21"/>',
  chevronsUpDown: '<path d="m7 15 5 5 5-5"/><path d="m7 9 5-5 5 5"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  chevronLeft: '<path d="m15 18-6-6 6-6"/>',
  menu: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/>',
};

export const OMEGA_ICON_NAMES = Object.keys(GLYPHS);

export function omegaIconSrc(name) {
  const glyph = GLYPHS[name];
  if (!glyph) throw new Error(`Unknown Omega icon: ${name}`);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${glyph}</svg>`;
  return `data:image/svg+xml;base64,${btoa(svg)}`;
}

export function OmegaIcon({ name, size = 20, sx, ...other }) {
  return (
    <SvgColor
      src={omegaIconSrc(name)}
      data-omega-icon={name}
      sx={{ width: size, height: size, flexShrink: 0, ...sx }}
      {...other}
    />
  );
}

OmegaIcon.propTypes = {
  name: PropTypes.oneOf(OMEGA_ICON_NAMES).isRequired,
  size: PropTypes.number,
  sx: PropTypes.object,
};

// Nav items receive their size and colour through cloneElement(icon, { sx }),
// so the nav icon must be a plain SvgColor element, as config-navigation's
// icon() helper produces for the host icons.
export function omegaNavIcon(name) {
  return <SvgColor src={omegaIconSrc(name)} data-omega-icon={name} />;
}
