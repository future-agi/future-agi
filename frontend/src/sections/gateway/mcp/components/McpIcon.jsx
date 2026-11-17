import React from "react";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";

// Glyphs the MCP pages need beyond the gateway shell's set
// (shell/gateway-icons.jsx): same 24 px grid, 1.5 px stroke, lucide shapes,
// inlined as data URIs so nothing is fetched at runtime (no Iconify).
const GLYPHS = {
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  refresh:
    '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  edit: '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/>',
  trash:
    '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  rotate:
    '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
  plug: '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
  layers:
    '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
};

export const MCP_ICON_NAMES = Object.keys(GLYPHS);

export function mcpIconSrc(name) {
  const glyph = GLYPHS[name];
  if (!glyph) throw new Error(`Unknown MCP icon: ${name}`);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${glyph}</svg>`;
  return `data:image/svg+xml;base64,${btoa(svg)}`;
}

export default function McpIcon({ name, size = 16, sx, ...other }) {
  return (
    <SvgColor
      src={mcpIconSrc(name)}
      data-mcp-icon={name}
      aria-hidden
      sx={{ width: size, height: size, flexShrink: 0, ...sx }}
      {...other}
    />
  );
}

McpIcon.propTypes = {
  name: PropTypes.oneOf(MCP_ICON_NAMES).isRequired,
  size: PropTypes.number,
  sx: PropTypes.object,
};
