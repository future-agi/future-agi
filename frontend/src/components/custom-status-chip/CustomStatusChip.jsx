import React from "react";
import PropTypes from "prop-types";
import { Chip, Typography, useTheme } from "@mui/material";
import SvgColor from "../svg-color";
import {
  getStatusDetails,
  getAvailableStatuses,
} from "../../utils/statusUtils";

const DARK_BG_MAP = {
  "green.o5": "green.o10",
  "red.o5": "red.o10",
};

const StatusChip = ({ label, status, disabled = false, ...otherProps }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const { finalLabel, config } = getStatusDetails({
    status,
    label,
  });

  const bgColor = isDark
    ? DARK_BG_MAP[config.bgColor] || config.bgColor
    : config.bgColor;

  const chipStyles = {
    color: config.textColor,
    backgroundColor: bgColor,
    borderWidth: "1px",
    borderStyle: "solid",
    pointerEvents: "none",
    height: "22px",
    paddingLeft: "4px",
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
      icon={chipIcon}
      sx={chipStyles}
      disabled={disabled}
      {...otherProps}
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
};

export default StatusChip;
