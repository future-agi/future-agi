import React from "react";
import PropTypes from "prop-types";
import { Tooltip } from "@mui/material";
import { GATEWAY_ADMIN_ONLY } from "../constants/gatewayAdmin";

// Wraps a gateway config write control that the caller has disabled because
// the user is not an Owner or Admin (useGatewayAdmin), so it says why on
// hover and to assistive tech. A disabled MUI button fires no events, so the
// tooltip sits on a wrapping span. With `allowed` the control is returned
// as it is.
const GatewayAdminOnly = ({ allowed, children, display = "inline-flex" }) => {
  if (allowed) return children;
  return (
    <Tooltip title={GATEWAY_ADMIN_ONLY}>
      <span style={{ display }}>{children}</span>
    </Tooltip>
  );
};

GatewayAdminOnly.propTypes = {
  allowed: PropTypes.bool.isRequired,
  children: PropTypes.node.isRequired,
  display: PropTypes.string,
};

export default GatewayAdminOnly;
