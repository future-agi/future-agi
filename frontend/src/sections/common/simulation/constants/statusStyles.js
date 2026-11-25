import { statusColors } from "src/utils/runStatus";

/**
 * Execution statuses (the values the API sends) and the role each takes from
 * the one status map (src/utils/runStatus). Cell renderers draw them with the
 * shared StatusBadge; nothing here holds a colour.
 */
export const EXECUTION_STATUS_ROLE = {
  NotStarted: "neutral",
  Pending: "neutral",
  Queued: "neutral",
  Running: "info",
  Completed: "success",
  Evaluating: "info",
  Editing: "info",
  Inactive: "warning",
  Failed: "danger",
  Cancelling: "neutral",
  Cancelled: "neutral",
};

const tint = (role) => ({
  backgroundColor: (theme) => statusColors(theme, role).bg,
  color: (theme) => statusColors(theme, role).fg,
});

/**
 * Chip sx for a status, resolved from theme.palette.status. Kept for callers
 * that still draw a Chip; new code uses StatusBadge.
 * Pending stays "info" here: the runs table reads Running from it.
 */
export const statusStyles = Object.fromEntries(
  Object.entries(EXECUTION_STATUS_ROLE).map(([status, role]) => [
    status,
    tint(status === "Pending" ? "info" : role),
  ]),
);

export const STOPPABLE_STATUSES = ["Evaluating", "Running", "Pending"];

export const TERMINAL_STATUSES = ["Completed", "Failed", "Cancelled"];

export default statusStyles;
