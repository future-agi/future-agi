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
  [STATUS_TYPES.RUNNING]: "info",
  [STATUS_TYPES.CANCELED]: "warning",
};

const StatusChip = ({
  label,
  status,
  disabled = false,
  neutralTextColor,
  sx,
  ...otherProps
}) => {
  const { finalLabel, finalStatus } = getStatusDetails({ status, label });
  // MUI Chip props callers used to pass through have no badge equivalent.
  // eslint-disable-next-line no-unused-vars
  const { size, variant, icon, onDelete, showIcon, ...rest } = otherProps;

  // Opt-in readability override for neutral (UNSET-classified) chips only. Callers
  // whose chip background was measured too close to `text.disabled` pass a
  // higher-contrast theme token; every other status and every disabled chip
  // keeps the badge colours. (TH-4088, scoped to the Observe span list.)
  const neutralOverride =
    neutralTextColor && !disabled && finalStatus === STATUS_TYPES.UNSET
      ? { "& .MuiTypography-root": { color: neutralTextColor } }
      : null;

  return (
    <StatusBadge
      status={finalStatus}
      role={ROLE[finalStatus] || "neutral"}
      label={finalLabel}
      sx={{
        opacity: disabled ? 0.48 : 1,
        pointerEvents: "none",
        ...neutralOverride,
        ...sx,
      }}
      {...rest}
    />
  );
};

StatusChip.propTypes = {
  label: PropTypes.string,
  showIcon: PropTypes.bool,
  neutralTextColor: PropTypes.string,
  status: PropTypes.oneOfType([
    PropTypes.oneOf([...getAvailableStatuses(), null, undefined]),
    PropTypes.string,
  ]),
  disabled: PropTypes.bool,
  sx: PropTypes.object,
};

export default StatusChip;
