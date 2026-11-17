import React from "react";
import PropTypes from "prop-types";

import { GatewayStatusChip } from "../../components/frame";

// The gateway's live status (the gateway list pings it on every read).
const STATUS_TONES = {
  healthy: { tone: "healthy", label: "Healthy" },
  connected: { tone: "healthy", label: "Healthy" },
  degraded: { tone: "warning", label: "Degraded" },
  unreachable: { tone: "error", label: "Unreachable" },
};

export default function GatewayHealthChip({ status }) {
  const known = STATUS_TONES[status];
  return (
    <GatewayStatusChip
      tone={known?.tone || "neutral"}
      label={known?.label || status || "Unknown"}
    />
  );
}

GatewayHealthChip.propTypes = { status: PropTypes.string };
