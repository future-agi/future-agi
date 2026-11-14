import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

export const EVAL_TASK_LOG_REQUEST_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

const invalidTaskLogResponse = () => {
  const error = new Error("Evaluation task logs returned an invalid response");
  error.code = "eval_task_logs_invalid_response";
  return error;
};

const isCount = (value) => Number.isSafeInteger(value) && value >= 0;

/** Read one bounded task-log summary without publishing malformed counters. */
export async function readEvalTaskLogs(requestLogs, upstreamSignal) {
  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestLogs({ signal, timeout: EVAL_TASK_LOG_REQUEST_TIMEOUT_MS }),
    {
      timeoutMs: EVAL_TASK_LOG_REQUEST_TIMEOUT_MS,
      signal: upstreamSignal,
    },
  );
  const body = response?.data;
  const result = body?.result;
  const counts = [
    result?.success_count,
    result?.errors_count,
    result?.skipped_count,
    result?.warnings_count,
    result?.total_count,
  ];
  const processed = counts
    .slice(0, 3)
    .reduce((total, value) => total + value, 0);

  if (
    body?.status !== true ||
    !result ||
    counts.some((value) => !isCount(value)) ||
    processed > result.total_count ||
    !Array.isArray(result.error_groups) ||
    !Array.isArray(result.warning_groups) ||
    typeof result.error_groups_truncated !== "boolean" ||
    typeof result.warning_groups_truncated !== "boolean" ||
    typeof result.status !== "string" ||
    result.status.length === 0 ||
    typeof result.row_type !== "string" ||
    result.row_type.length === 0
  ) {
    throw invalidTaskLogResponse();
  }

  return result;
}
