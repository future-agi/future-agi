import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { readEvalTaskLogs } from "./task_log_read";

// One cache entry per task: Logs and the task header read the same summary.
export const evalTaskLogsQueryKey = (taskId) => ["eval-task-logs", taskId];

export const evalTaskLogsQueryFn =
  (taskId) =>
  ({ signal }) =>
    readEvalTaskLogs(
      ({ signal: requestSignal, timeout }) =>
        axios.get(endpoints.project.getEvalTaskLogs(), {
          signal: requestSignal,
          timeout,
          params: { eval_task_id: taskId },
        }),
      signal,
    );

// The header reads the summary for a Failed task; opening Logs right after
// reuses it instead of fetching the same summary twice. Polling is unaffected.
export const EVAL_TASK_LOGS_STALE_MS = 5_000;

// The backend's start_refusal codes for a first start it confirmed failed
// after the automatic retry. Plain Resume is refused there; Re-run is not.
export const CONFIRMED_START_FAILURE_CODES = ["start_failed", "exhausted"];

export const confirmedStartFailureCode = (logs) =>
  logs?.error_groups?.find((group) =>
    CONFIRMED_START_FAILURE_CODES.includes(group?.start_reason_code),
  )?.start_reason_code ?? null;

/** Whether this failed task's start was confirmed failed (Resume is refused). */
export function useConfirmedStartFailure(taskId, status) {
  const { data } = useQuery({
    queryKey: evalTaskLogsQueryKey(taskId),
    queryFn: evalTaskLogsQueryFn(taskId),
    enabled: Boolean(taskId) && status === "failed",
    retry: false,
    staleTime: EVAL_TASK_LOGS_STALE_MS,
  });
  return status === "failed" && Boolean(confirmedStartFailureCode(data));
}
