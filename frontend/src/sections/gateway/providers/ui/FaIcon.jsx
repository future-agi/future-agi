import React from "react";
import PropTypes from "prop-types";

import SvgColor from "src/components/svg-color";

// Future AGI's own outline icons (public/assets/icons), painted in the
// current colour at 16 px. Local files only: nothing is fetched from an icon
// service, so they render offline and in screenshots.
const SOURCES = {
  add: "/assets/icons/ic_add.svg",
  copy: "/assets/icons/ic_copy.svg",
  delete: "/assets/icons/ic_delete.svg",
  edit: "/assets/icons/ic_edit.svg",
  more: "/assets/icons/ic_ellipsis.svg",
  open: "/assets/icons/ic_arrow_top_right.svg",
  reload: "/assets/icons/ic_reload.svg",
  check: "/assets/icons/ic_check.svg",
};

export const FA_ICON_NAMES = Object.keys(SOURCES);

export default function FaIcon({ name, size = 16, sx = {}, ...other }) {
  return (
    <SvgColor
      src={SOURCES[name]}
      aria-hidden
      data-fa-icon={name}
      sx={{ width: size, height: size, flexShrink: 0, ...sx }}
      {...other}
    />
  );
}

FaIcon.propTypes = {
  name: PropTypes.oneOf(FA_ICON_NAMES).isRequired,
  size: PropTypes.number,
  sx: PropTypes.object,
};
