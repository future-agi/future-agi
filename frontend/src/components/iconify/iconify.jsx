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
