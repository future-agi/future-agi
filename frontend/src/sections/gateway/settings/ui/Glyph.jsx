import React from "react";
import PropTypes from "prop-types";

import SvgColor from "src/components/svg-color";

import { gatewayIconSrc } from "../../shell/gateway-icons";

// A 16 px glyph from the app's own assets (or the gateway glyph set), in
// place of Iconify, which fetches icons at runtime and draws nothing offline.
const SOURCES = {
  add: "/assets/icons/ic_add.svg",
  close: "/assets/icons/ic_close.svg",
  delete: "/assets/icons/ic_delete.svg",
  edit: "/assets/icons/ic_edit.svg",
  email: "/assets/icons/ic_email.svg",
};

export default function Glyph({ name, size = 16, sx }) {
  // The arrow is the gateway's back glyph turned to point forward.
  const arrow = name === "arrow";
  return (
    <SvgColor
      src={arrow ? gatewayIconSrc("back") : SOURCES[name]}
      data-glyph={name}
      aria-hidden
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        ...(arrow ? { transform: "rotate(180deg)" } : null),
        ...sx,
      }}
    />
  );
}

Glyph.propTypes = {
  name: PropTypes.oneOf(["add", "close", "delete", "edit", "email", "arrow"])
    .isRequired,
  size: PropTypes.number,
  sx: PropTypes.object,
};
