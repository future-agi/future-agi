import React from "react";
import PropTypes from "prop-types";
import { Tooltip } from "@mui/material";

// Wraps a control the caller has disabled for a reason other than the role
// (GatewayAdminOnly covers that one), so it says why on hover and to
// assistive tech. A disabled MUI button fires no events, so the tooltip sits
// on a wrapping span. With no `reason` the control is returned as it is.
const WhyDisabled = ({ reason, children, display = "inline-flex" }) => {
  if (!reason) return children;
  return (
    <Tooltip title={reason}>
      <span style={{ display }}>{children}</span>
    </Tooltip>
  );
};

WhyDisabled.propTypes = {
  reason: PropTypes.string,
  children: PropTypes.node.isRequired,
  display: PropTypes.string,
};

export default WhyDisabled;
