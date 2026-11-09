import React from "react";
import PropTypes from "prop-types";
import { Tooltip } from "@mui/material";

// Wraps a control the caller has disabled for a reason the user should see
// (guardrails off on this gateway, a list a check needs left empty), so it
// says why on hover and to assistive tech. A disabled MUI button fires no
// events, so the tooltip sits on a wrapping span. With no reason the control
// is returned as it is.
const DisabledReason = ({ reason, children, display = "inline-flex" }) => {
  if (!reason) return children;
  return (
    <Tooltip title={reason}>
      <span style={{ display }}>{children}</span>
    </Tooltip>
  );
};

DisabledReason.propTypes = {
  reason: PropTypes.string,
  children: PropTypes.node.isRequired,
  display: PropTypes.string,
};

export default DisabledReason;
