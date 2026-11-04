import PropTypes from "prop-types";
import RunStatusBadge from "src/components/status-badge/StatusBadge";

const STATUS_CONFIG = {
  pending: { label: "Pending Annotation", role: "neutral" },
  in_progress: { label: "In Progress", role: "info" },
  in_review: { label: "In Review", role: "warning" },
  needs_changes: { label: "Needs changes", role: "warning" },
  resubmitted: { label: "Resubmitted", role: "info" },
  completed: { label: "Completed", role: "success" },
  skipped: { label: "Skipped", role: "neutral" },
};

export default function ItemStatusBadge({ status, label }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  return (
    <RunStatusBadge
      status={status}
      label={label || config.label}
      role={config.role}
    />
  );
}

ItemStatusBadge.propTypes = {
  label: PropTypes.string,
  status: PropTypes.string,
};
