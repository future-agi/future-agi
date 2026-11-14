import PropTypes from "prop-types";
import RunStatusBadge from "src/components/status-badge/StatusBadge";

const STATUS_CONFIG = {
  draft: { label: "Draft", role: "neutral" },
  active: { label: "Active", role: "success" },
  paused: { label: "Paused", role: "warning" },
  completed: { label: "Completed", role: "success" },
};

StatusBadge.propTypes = {
  status: PropTypes.string,
};

export default function StatusBadge({ status }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.draft;
  return (
    <RunStatusBadge status={status} label={config.label} role={config.role} />
  );
}
