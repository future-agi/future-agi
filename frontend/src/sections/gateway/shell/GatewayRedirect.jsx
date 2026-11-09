import React from "react";
import PropTypes from "prop-types";
import { Navigate, useLocation } from "react-router-dom";

// Sends an old or hidden gateway URL to its new home, keeping the query and
// the hash (a Stripe checkout return or a bookmarked filter survives).
export default function GatewayRedirect({ to }) {
  const location = useLocation();
  return (
    <Navigate
      replace
      to={{ pathname: to, search: location.search, hash: location.hash }}
    />
  );
}

GatewayRedirect.propTypes = {
  to: PropTypes.string.isRequired,
};
