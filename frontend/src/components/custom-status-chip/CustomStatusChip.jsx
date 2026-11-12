import React from "react";
import PropTypes from "prop-types";
import StatusBadge from "../status-badge/StatusBadge";
import {
  getStatusDetails,
  getAvailableStatuses,
  STATUS_TYPES,
} from "../../utils/statusUtils";

// DS v0.2.3: neutral dot + label, tinted badges for the other status roles.
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
  showIcon = true,
  neutralTextColor,
  sx,
  ...otherProps
}) => {
  const { finalLabel, finalStatus } = getStatusDetails({ status, label });
  // Chip-only props have no badge equivalent; never forward them to the DOM.
  // eslint-disable-next-line no-unused-vars
  const { size, variant, icon, onDelete, ...rest } = otherProps;

  // Only enabled UNSET chips opt in. All other chips keep the badge colours.
  const neutralOverride =
    neutralTextColor && !disabled && finalStatus === STATUS_TYPES.UNSET
      ? { "& .MuiTypography-root": { color: neutralTextColor } }
      : null;

  return (
    <StatusBadge
      {...rest}
      status={finalStatus}
      role={ROLE[finalStatus] || "neutral"}
      label={finalLabel}
      dot={showIcon}
      // Consume disabled here: the legacy badge disabled prop replaces colours.
      // Dim instead, while retaining the accessible disabled state.
      aria-disabled={disabled || undefined}
      sx={[
        {
          opacity: disabled ? 0.48 : 1,
          pointerEvents: "none",
          ...neutralOverride,
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
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
  sx: PropTypes.oneOfType([PropTypes.object, PropTypes.array, PropTypes.func]),
};

export default StatusChip;
