import React from "react";
import { Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import PropTypes from "prop-types";

const STATUS_CONFIG = {
  escalating: {
    label: "Escalating",
    tip: "Not triaged yet, and happening more often. No epoch has started.",
    icon: "solar:graph-up-linear",
  },
  acknowledged: {
    label: "Acknowledged",
    tip: "Someone on the team has seen it. No epoch has started.",
    icon: "solar:check-circle-linear",
  },
  for_review: {
    label: "For review",
    tip: "Flagged for a person to look at and decide what to do. No epoch has started.",
    icon: "solar:eye-linear",
  },
  resolved: {
    label: "Resolved",
    tip: "Marked resolved by hand.",
    icon: "solar:check-circle-bold",
  },
  ignored: {
    label: "Ignored",
    tip: "Muted: it stays in the list but stops alerting and isn't counted as escalating. Change the status to bring it back.",
    icon: "solar:bell-off-linear",
  },
};

export default function ErrorStatusChip({ status, size = "small", onClick }) {
  const key = STATUS_CONFIG[status] ? status : "escalating";
  const cfg = STATUS_CONFIG[key];

  return (
    <Tooltip arrow title={cfg.tip}>
      <span style={{ display: "inline-flex" }}>
        <StatusBadge
          status={key}
          label={cfg.label}
          size={size === "medium" ? "md" : "sm"}
          icon={<Iconify icon={cfg.icon} width={12} />}
          onClick={onClick}
          sx={
            onClick
              ? { cursor: "pointer", "&:hover": { opacity: 0.85 } }
              : undefined
          }
        />
      </span>
    </Tooltip>
  );
}

ErrorStatusChip.propTypes = {
  status: PropTypes.oneOf([
    "escalating",
    "acknowledged",
    "for_review",
    "resolved",
    "ignored",
  ]),
  size: PropTypes.oneOf(["small", "medium"]),
  onClick: PropTypes.func,
};
