import PropTypes from "prop-types";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { STATUS_META, LIVE_STATUSES, PULSING_DOT_CLASS } from "./runs.constants";

// Statuses this area uses that the shared status map doesn't name yet.
const ROLE_FALLBACK = {
  inconclusive: "neutral",
  finished: "neutral",
  cancelling: "neutral",
};

// Design system: every run status renders through the shared StatusBadge and
// the one status map (src/utils/runStatus.js), so it matches every other screen.
export default function StatusChip({ status, label }) {
  const meta = STATUS_META[status] || STATUS_META.queued;
  const key = status === "finished" ? "completed" : status;
  return (
    <StatusBadge
      status={key}
      label={label ?? meta.label}
      role={ROLE_FALLBACK[status]}
      className={LIVE_STATUSES.includes(status) ? PULSING_DOT_CLASS : undefined}
    />
  );
}

StatusChip.propTypes = {
  status: PropTypes.string,
  // Overrides the status's own label, for a caller whose vocabulary differs.
  label: PropTypes.string,
};
