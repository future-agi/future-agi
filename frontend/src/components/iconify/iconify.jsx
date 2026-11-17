import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";
import { Icon } from "./offline";
import { registerOfflineIcon as addIcon } from "./registry";

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
// Icons resolve from the bundled registry; unknown names never fetch remotely.
const Iconify = forwardRef(
  /** @param {Omit<import("@iconify/react").IconProps, "icon" | "width" | "height" | "ref"> & Omit<import("@mui/material").BoxProps<"svg">, "component" | "ref"> & { icon?: import("@iconify/react").IconProps["icon"] }} props */ (
    { icon, width = 16, sx, ...other },
    ref,
  ) => (
    <Box
      ref={ref}
      component={Icon}
      className="component-iconify"
      icon={icon}
      sx={{ width, height: width, ...sx }}
      {...other}
    >
      <Box
        component="span"
        aria-hidden
        className="component-iconify component-iconify-placeholder"
        sx={{
          display: "inline-block",
          flexShrink: 0,
          width,
          height: width,
          ...sx,
        }}
      />
    </Box>
  ),
);

Iconify.displayName = "Iconify";

Iconify.propTypes = /** @type {typeof Iconify.propTypes} */ (
  /** @type {unknown} */ ({
    icon: PropTypes.oneOfType([
      PropTypes.shape({ body: PropTypes.string.isRequired }),
      PropTypes.string,
    ]),
    sx: PropTypes.oneOfType([
      PropTypes.object,
      PropTypes.array,
      PropTypes.func,
    ]),
    width: PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.number,
      PropTypes.object,
    ]),
  })
);

export default Iconify;
