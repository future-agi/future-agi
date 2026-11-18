import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

export const EVAL_TASK_LIST_REQUEST_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

const invalidTaskPage = () => {
  const error = new Error("Evaluation tasks returned an invalid page");
  error.code = "eval_task_list_invalid_page";
  return error;
};

/** Bound and validate one task-list page before a grid can publish it. */
export async function readEvalTaskListPage(requestPage, upstreamSignal) {
  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestPage({
        signal,
        timeout: EVAL_TASK_LIST_REQUEST_TIMEOUT_MS,
      }),
    {
      timeoutMs: EVAL_TASK_LIST_REQUEST_TIMEOUT_MS,
      signal: upstreamSignal,
    },
  );
  const result = response?.data?.result;
  const totalRows = result?.metadata?.total_rows;
  if (
    !result ||
    !Array.isArray(result.table) ||
    !Number.isSafeInteger(totalRows) ||
    totalRows < 0 ||
    result.table.length > totalRows
  ) {
    throw invalidTaskPage();
  }
  return { ...result, table: result.table, totalRows };
}
