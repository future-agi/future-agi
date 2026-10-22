import React from "react";
import PropTypes from "prop-types";
import { Chip, Typography, useTheme } from "@mui/material";
import SvgColor from "../svg-color";
import {
  getStatusDetails,
  getAvailableStatuses,
  STATUS_TYPES,
} from "../../utils/statusUtils";

const DARK_BG_MAP = {
  "green.o5": "green.o10",
  "red.o5": "red.o10",
  "blue.o5": "blue.o10",
  "orange.o5": "orange.o10",
};

// Deep shades chosen for legibility on a light page read at roughly 2:1 on the dark one,
// so they lighten. Only tokens listed here change; every other status keeps its value.
const DARK_TEXT_MAP = {
  "orange.700": "orange.300",
};

const StatusChip = ({
  label,
  status,
  disabled = false,
  showIcon = true,
  neutralTextColor,
  ...otherProps
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const { finalLabel, finalStatus, config } = getStatusDetails({
    status,
    label,
  });

  const bgColor = isDark
    ? DARK_BG_MAP[config.bgColor] || config.bgColor
    : config.bgColor;

  const defaultTextColor = isDark
    ? DARK_TEXT_MAP[config.textColor] || config.textColor
    : config.textColor;

  // Opt-in readability override for neutral (UNSET-classified) chips only. Callers whose
  // chip background was measured too close to `text.disabled` pass a higher-contrast theme
  // token; every other status, every disabled chip and every caller that omits the prop keep
  // the shared STATUS_CONFIG colours. (TH-4088, scoped to the Observe span list.)
  const textColor =
    neutralTextColor && !disabled && finalStatus === STATUS_TYPES.UNSET
      ? neutralTextColor
      : defaultTextColor;

  const chipStyles = {
    color: textColor,
    backgroundColor: bgColor,
    borderWidth: "1px",
    borderStyle: "solid",
    pointerEvents: "none",
    height: "22px",
    paddingLeft: showIcon ? "4px" : "2px",
    borderColor: config.borderColor,
    "& .MuiChip-icon": {
      color: config.color,
      marginLeft: "4px",
      marginRight: "4px",
    },
    "& .MuiChip-label": {
      paddingLeft: "6px",
      paddingRight: "8px",
      fontWeight: 400,
      paddingTop: "2px",
      paddingBottom: "2px",
    },
  };

  const chipIcon = (
    <SvgColor
      src={config.icon}
      sx={{
        width: "12px",
        height: "12px",
        fontColor: "text.disabled",
      }}
    />
  );

  return (
    <Chip
      label={
        <Typography variant="s3" fontWeight={"fontWeightRegular"}>
          {finalLabel}
        </Typography>
      }
      icon={showIcon ? chipIcon : undefined}
      sx={chipStyles}
      disabled={disabled}
      {...otherProps}
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
};

export default StatusChip;
