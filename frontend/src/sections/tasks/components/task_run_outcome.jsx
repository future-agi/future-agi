import PropTypes from "prop-types";
import { Button, Stack } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import StatusBadge from "src/components/status-badge";
import { readEvalTaskLogs } from "src/sections/common/EvalsTasks/task_log_read";
import axios, { endpoints } from "src/utils/axios";
import { taskRunOutcome } from "./taskRunOutcome";

// Reads the Logs tab's own query (same key and reader) once, for a completed
// task only, so opening Logs afterwards reuses it. A run with no evaluation
// runs has empty logs, so it links to the sampling audit instead (when the
// task samples), which says what was picked.
export default function TaskRunOutcome({
  taskId,
  status,
  onViewLogs,
  onViewSamplingAudit,
}) {
  const completed = (status || "").toLowerCase() === "completed";
  const { data } = useQuery({
    queryKey: ["eval-task-logs", taskId],
    queryFn: ({ signal }) =>
      readEvalTaskLogs(
        ({ signal: requestSignal, timeout }) =>
          axios.get(endpoints.project.getEvalTaskLogs(), {
            signal: requestSignal,
            timeout,
            params: { eval_task_id: taskId },
          }),
        signal,
      ),
    enabled: Boolean(taskId) && completed,
    retry: false,
    // A hint, not a page: the Logs tab reports its own read failures.
    meta: { errorHandled: true },
  });
  const outcome = completed ? taskRunOutcome(data) : null;
  if (!outcome) return null;
  const link = outcome.empty
    ? onViewSamplingAudit && {
        label: "View sampling audit",
        onClick: onViewSamplingAudit,
      }
    : onViewLogs && { label: "View logs", onClick: onViewLogs };
  return (
    <Stack
      direction="row"
      spacing={0.5}
      alignItems="center"
      aria-label="Last run outcome"
      sx={{ minWidth: 0 }}
    >
      <StatusBadge
        status={outcome.role}
        role={outcome.role}
        label={outcome.label}
      />
      {link && (
        <Button
          size="small"
          color="inherit"
          onClick={link.onClick}
          sx={{ minWidth: 0, px: 0.75, color: "text.secondary" }}
        >
          {link.label}
        </Button>
      )}
    </Stack>
  );
}
TaskRunOutcome.propTypes = {
  taskId: PropTypes.string,
  status: PropTypes.string,
  onViewLogs: PropTypes.func,
  // For a sampled task: where a run that evaluated nothing points.
  onViewSamplingAudit: PropTypes.func,
};
