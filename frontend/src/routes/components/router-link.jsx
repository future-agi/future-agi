import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";
import { Link } from "react-router-dom";

import { preloadRoute } from "../preload";

// ----------------------------------------------------------------------

// Hover or focus starts loading the target page's code (routes/preload.js).
/**
 * react-router's Link, addressed by `href` in place of `to`, as MUI's
 * `component` passes it.
 * @typedef {Omit<import("react-router-dom").LinkProps, "to"> & { href: string }} RouterLinkProps
 */

// eslint-disable-next-line react/display-name
const RouterLink = forwardRef(
  /**
   * @param {RouterLinkProps} props
   * @param {import("react").ForwardedRef<HTMLAnchorElement>} ref
   */
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
