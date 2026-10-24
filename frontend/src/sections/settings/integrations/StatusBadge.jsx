import PropTypes from "prop-types";
import RunStatusBadge from "src/components/status-badge/StatusBadge";

/*
  Integration state, drawn by the shared badge: the tint and dot come from
  theme.palette.status via the one status map.
*/
const STATUS_CONFIG = {
  active: { role: "success", label: "Active" },
  syncing: { role: "info", label: "Syncing" },
  paused: { role: "warning", label: "Paused" },
  error: { role: "danger", label: "Error" },
  backfilling: { role: "info", label: "Backfilling" },
};

export default function StatusBadge({ status }) {
  const config = STATUS_CONFIG[status] || {
    role: "neutral",
    label: status || "Unknown",
  };
  return (
    <RunStatusBadge status={status} label={config.label} role={config.role} />
  );
}

StatusBadge.propTypes = {
  status: PropTypes.string,
};
