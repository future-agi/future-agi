import { Chip } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "src/components/svg-color";

const getStatusConfig = (status) => {
  const configs = {
    all: {
      icon: "/assets/icons/ic_tick.svg",
      label: "All Mapped",
    },
    partial: {
      icon: "/assets/icons/ic_warning.svg",
      label: "Partially Mapped",
    },
    none: {
      icon: "/assets/icons/ic_failed.svg",
      label: "Not Mapped",
    },
  };
  const colorMap = {
    all: { color: "status.success.fg", bgColor: "status.success.bg" },
    partial: { color: "status.warning.fg", bgColor: "status.warning.bg" },
    none: { color: "status.danger.fg", bgColor: "status.danger.bg" },
  };
  return { ...configs[status], ...colorMap[status] };
};

const ChipForVariables = ({ status }) => {
  const { icon, label, bgColor, color } = getStatusConfig(status);
  return (
    <Chip
      size="small"
      label={label}
      sx={{
        backgroundColor: bgColor,
        color: color,
        "& .MuiChip-icon": {
          color: color,
        },
        ":hover": {
          backgroundColor: bgColor,
          color: color,
        },
      }}
      icon={<SvgColor width={16} src={icon} />}
    />
  );
};

export default ChipForVariables;
ChipForVariables.propTypes = {
  status: PropTypes.string,
};
