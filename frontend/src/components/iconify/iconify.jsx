import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";
import { Icon } from "@iconify/react";

import Box from "@mui/material/Box";

// ----------------------------------------------------------------------

/**
 * A wrapper component for displaying Iconify icons with customizable width and styling
 * Get Iconify icons from https://iconify.design/
 */
// Icons resolve from the bundled registry; unknown names never fetch remotely.
const Iconify = forwardRef(
  /** @param {Omit<import("@iconify/react").IconProps, "icon" | "width" | "height" | "ref"> & Omit<import("@mui/material").BoxProps<"svg">, "component" | "ref"> & { icon?: import("@iconify/react").IconProps["icon"] }} props */ (
    { icon, width = 20, sx, ...other },
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
