import React from "react";
import PropTypes from "prop-types";
import _ from "lodash";
import SvgColor from "src/components/svg-color";
import StatusBadge from "src/components/status-badge/StatusBadge";

// Each call state: its role in the one status map, and its icon.
const callStates = {
  completed: {
    role: "success",
    icon: "/assets/icons/agent/call_completed.svg",
  },
  failed: { role: "danger", icon: "/assets/icons/ic_failed.svg" },
  pending: { role: "neutral", icon: "/assets/icons/ic_call_pending.svg" },
  registered: {
    role: "neutral",
    icon: "/assets/icons/ic_call_registered.svg",
  },
  ongoing: { role: "info", icon: "/assets/icons/ic_call_ongoing.svg" },
  cancelled: { role: "neutral", icon: "/assets/icons/ic_call_cancelled.svg" },
  evaluating: {
    role: "info",
    icon: "/assets/icons/agent/call_completed.svg",
  },
  running: { role: "info", icon: "/assets/icons/ic_call_running.svg" },
  "in-progress": {
    role: "info",
    icon: "/assets/icons/navbar/ic_new_clock.svg",
  },
  analyzing: { role: "info", icon: "/assets/icons/app/ic_search.svg" },
  queued: { role: "neutral", icon: "/assets/icons/ic_call_queued.svg" },
  default: { role: "neutral", icon: "/assets/icons/ic_call_pending.svg" },
};

const CallStatus = ({ value }) => {
  const key = value?.toLowerCase();

  const state = callStates[key] ?? callStates.default;

  return (
    <StatusBadge
      status={value}
      label={_.capitalize(value)}
      role={state.role}
      icon={<SvgColor sx={{ width: 14, height: 14 }} src={state.icon} />}
    />
  );
};

CallStatus.propTypes = {
  value: PropTypes.string,
};

export default CallStatus;
