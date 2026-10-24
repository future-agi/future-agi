import React from "react";
import PropTypes from "prop-types";
import StatusBadge from "../status-badge/StatusBadge";
import {
  getStatusDetails,
  getAvailableStatuses,
  STATUS_TYPES,
} from "../../utils/statusUtils";

// Drawn as the design system's status badge: tinted, no coloured outline.
const ROLE = {
  [STATUS_TYPES.PASS]: "success",
  [STATUS_TYPES.ERROR]: "danger",
  [STATUS_TYPES.UNSET]: "neutral",
};

const StatusChip = ({ label, status, disabled = false, sx, ...otherProps }) => {
  const { finalLabel, finalStatus } = getStatusDetails({ status, label });
  // MUI Chip props callers used to pass through have no badge equivalent.
  // eslint-disable-next-line no-unused-vars
  const { size, variant, icon, onDelete, ...rest } = otherProps;

  return (
    <StatusBadge
      status={finalStatus}
      role={ROLE[finalStatus] || "neutral"}
      label={finalLabel}
      sx={{ opacity: disabled ? 0.48 : 1, pointerEvents: "none", ...sx }}
      {...rest}
    />
  );
};

StatusChip.propTypes = {
  label: PropTypes.string,
  status: PropTypes.oneOfType([
    PropTypes.oneOf([...getAvailableStatuses(), null, undefined]),
    PropTypes.string,
  ]),
  disabled: PropTypes.bool,
  sx: PropTypes.object,
};

export default StatusChip;
