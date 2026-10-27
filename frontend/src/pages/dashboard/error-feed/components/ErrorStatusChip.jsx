import React from "react";
import { Chip, useTheme } from "@mui/material";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";

const STATUS_CONFIG = {
  escalating: {
    label: "Escalating",
    icon: "mdi:trending-up",
    color: "#DB2F2D",
    bg: "#FCE8E7",
    darkBg: "rgba(219,47,45,0.15)",
    darkColor: "#E87878",
  },
  acknowledged: {
    label: "Acknowledged",
    icon: "mdi:check-circle-outline",
    color: "#605C70",
    bg: "#F1F0F5",
    darkBg: "rgba(148,143,163,0.15)",
    darkColor: "#938FA3",
  },
  for_review: {
    label: "For review",
    icon: "mdi:eye-outline",
    color: "#8C3F08",
    bg: "#FDEEE2",
    darkBg: "rgba(245,166,35,0.15)",
    darkColor: "#F5A623",
  },
  resolved: {
    label: "Resolved",
    icon: "mdi:check-circle",
    color: "#005F2F",
    bg: "#E0F7EC",
    darkBg: "rgba(90,206,109,0.15)",
    darkColor: "#5ACE6D",
  },
};

export default function ErrorStatusChip({ status, size = "small", onClick }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.escalating;

  const baseBg = isDark ? cfg.darkBg : cfg.bg;
  return (
    <Chip
      size={size}
      {...(onClick ? { onClick } : {})}
      icon={
        <Iconify
          icon={cfg.icon}
          width={12}
          sx={{
            color: `${isDark ? cfg.darkColor : cfg.color} !important`,
            ml: "6px !important",
          }}
        />
      }
      label={cfg.label}
      sx={{
        height: 22,
        borderRadius: "4px",
        fontSize: "11px",
        fontWeight: 500,
        letterSpacing: "0.01em",
        color: isDark ? cfg.darkColor : cfg.color,
        bgcolor: baseBg,
        border: "1px solid",
        borderColor: isDark ? `${cfg.darkColor}40` : `${cfg.color}30`,
        cursor: onClick ? "pointer" : "default",
        "& .MuiChip-label": { px: "6px", lineHeight: 1 },
        "&:hover": onClick ? { opacity: 0.85 } : { bgcolor: baseBg },
        "&:focus": { bgcolor: baseBg },
      }}
    />
  );
}

ErrorStatusChip.propTypes = {
  status: PropTypes.oneOf([
    "escalating",
    "acknowledged",
    "for_review",
    "resolved",
  ]),
  size: PropTypes.oneOf(["small", "medium"]),
  onClick: PropTypes.func,
};
