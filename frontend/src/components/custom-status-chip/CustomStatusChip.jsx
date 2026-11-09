import React from "react";
import PropTypes from "prop-types";
import { Chip, Typography } from "@mui/material";
import SvgColor from "../svg-color";
import { getStatusDetails, STATUS_TYPES } from "../../utils/statusUtils";
import { dsSize, dsTypography } from "../../theme/ds-tokens";

// Drop-in legacy adapter. Keep Chip callbacks, tooltip labels and Observe's
// showIcon/neutralTextColor props without forwarding those props to the DOM.
export default function StatusChip({
  label,
  status,
  disabled = false,
  showIcon = true,
  neutralTextColor,
  sx,
  ...otherProps
}) {
  const { finalLabel, finalStatus, config } = getStatusDetails({
    status,
    label,
  });
  const textColor =
    neutralTextColor && !disabled && finalStatus === STATUS_TYPES.UNSET
      ? neutralTextColor
      : config.textColor;
  return (
    <Chip
      {...otherProps}
      disabled={disabled}
      label={
        <Typography
          component="span"
          sx={{ ...dsTypography.label_sm, color: "inherit" }}
        >
          {finalLabel}
        </Typography>
      }
      icon={
        showIcon ? (
          <SvgColor src={config.icon} sx={{ width: 12, height: 12 }} />
        ) : undefined
      }
      sx={[
        {
          color: textColor,
          pointerEvents: "none",
          bgcolor: config.bgColor,
          border: 0,
          height: dsSize.badge.md,
          "& .MuiChip-icon": { color: config.color, ml: 0.75, mr: 0 },
          "& .MuiChip-label": { px: 0.75 },
          "&.Mui-disabled": {
            color: "text.disabled",
            bgcolor: "action.disabledBackground",
          },
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    />
  );
}
StatusChip.propTypes = {
  label: PropTypes.string,
  status: PropTypes.string,
  disabled: PropTypes.bool,
  showIcon: PropTypes.bool,
  neutralTextColor: PropTypes.string,
  sx: PropTypes.oneOfType([PropTypes.object, PropTypes.array, PropTypes.func]),
};
