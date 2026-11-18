import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";
import { Link } from "react-router-dom";

import { preloadRoute } from "../preload";

// ----------------------------------------------------------------------

// Hover or focus starts loading the target page's code (routes/preload.js).
// eslint-disable-next-line react/display-name
const RouterLink = forwardRef(
  ({ href, onMouseEnter, onFocus, ...other }, ref) => (
    <Link
      ref={ref}
      to={href}
      onMouseEnter={(event) => {
        preloadRoute(href);
        onMouseEnter?.(event);
      }}
      onFocus={(event) => {
        preloadRoute(href);
        onFocus?.(event);
      }}
      {...other}
    />
  ),
);

RouterLink.propTypes = {
  href: PropTypes.string,
  onMouseEnter: PropTypes.func,
  onFocus: PropTypes.func,
};

export default RouterLink;
