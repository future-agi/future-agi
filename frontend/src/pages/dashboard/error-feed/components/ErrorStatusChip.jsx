import React from "react";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import PropTypes from "prop-types";

const STATUS_CONFIG = {
  escalating: {
    label: "Escalating",
    icon: "solar:graph-up-linear",
  },
  acknowledged: {
    label: "Acknowledged",
    icon: "solar:check-circle-linear",
  },
  for_review: {
    label: "For review",
    icon: "solar:eye-linear",
  },
  resolved: {
    label: "Resolved",
    icon: "solar:check-circle-bold",
  },
};

export default function ErrorStatusChip({ status, size = "small", onClick }) {
  const key = STATUS_CONFIG[status] ? status : "escalating";
  const cfg = STATUS_CONFIG[key];

  return (
    <StatusBadge
      status={key}
      label={cfg.label}
      size={size === "medium" ? "md" : "sm"}
      icon={<Iconify icon={cfg.icon} width={12} />}
      onClick={onClick}
      sx={
        onClick ? { cursor: "pointer", "&:hover": { opacity: 0.85 } } : undefined
      }
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
