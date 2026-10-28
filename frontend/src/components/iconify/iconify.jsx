import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";
import { Icon, addIcon } from "@iconify/react";

import Box from "@mui/material/Box";

// ----------------------------------------------------------------------

/*
  Solar has no plain outline circle — the API answers `not_found` for
  `solar:circle-linear`, so every "unselected" state that used it rendered
  as an empty gap. Registered here, drawn like Solar's own circle icons
  (the ring of `check-circle-linear`), so it resolves locally everywhere.
*/
addIcon("solar:circle-linear", {
  body: '<circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  width: 24,
  height: 24,
});

/* Same for the plain outline square (unchecked checkbox state). */
addIcon("solar:square-linear", {
  body: '<rect x="3" y="3" width="18" height="18" rx="4" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  width: 24,
  height: 24,
});

/*
  Fix — a wrench. Solar has none, so it's drawn here in Solar's linear style
  (24 grid, 1.5 stroke, round ends; shape adapted from Lucide's, ISC).
*/
addIcon("fagi:fix", {
  body: '<path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  width: 24,
  height: 24,
});

/**
 * A wrapper component for displaying Iconify icons with customizable width and styling
 * Get Iconify icons from https://iconify.design/
 */
const Iconify = forwardRef(({ icon, width = 20, sx, ...other }, ref) => (
  <Box
    ref={ref}
    component={Icon}
    className="component-iconify"
    icon={icon}
    sx={{ width, height: width, ...sx }}
    {...other}
  />
));

Iconify.displayName = "Iconify";

Iconify.propTypes = {
  icon: PropTypes.oneOfType([PropTypes.element, PropTypes.string]),
  sx: PropTypes.object,
  width: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

export default Iconify;
