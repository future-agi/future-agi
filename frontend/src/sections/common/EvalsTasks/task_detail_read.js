import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

export const EVAL_TASK_DETAIL_REQUEST_TIMEOUT_MS =
  INTERACTIVE_REQUEST_TIMEOUT_MS;

const invalidTaskDetailResponse = () => {
  const error = new Error(
    "Evaluation task details returned an invalid response",
  );
  error.code = "eval_task_detail_invalid_response";
  return error;
};

const isRecord = (value) =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const isNonEmptyString = (value) =>
  typeof value === "string" && value.length > 0;

const isNullableString = (value) => value === null || typeof value === "string";

const isNullableFiniteNumber = (value) =>
  value === null || (typeof value === "number" && Number.isFinite(value));

/** Read one task detail under the visible-action wall and keep cache shape. */
export async function readEvalTaskDetail(requestTask, upstreamSignal) {
  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestTask({
        signal,
        timeout: EVAL_TASK_DETAIL_REQUEST_TIMEOUT_MS,
      }),
    {
      timeoutMs: EVAL_TASK_DETAIL_REQUEST_TIMEOUT_MS,
      signal: upstreamSignal,
    },
  );
  const body = response?.data;
  const result = body?.result;

  if (
    body?.status !== true ||
    !isRecord(result) ||
    !isNonEmptyString(result.id) ||
    !isNullableString(result.name) ||
    !isNonEmptyString(result.project_id) ||
    typeof result.project_name !== "string" ||
    !isNullableString(result.status) ||
    !(result.filters_applied === null || isRecord(result.filters_applied)) ||
    !Array.isArray(result.evals_applied) ||
    !isNullableFiniteNumber(result.spans_limit) ||
    !isNullableFiniteNumber(result.sampling_rate) ||
    !isNullableString(result.run_type) ||
    !isNonEmptyString(result.row_type)
  ) {
    throw invalidTaskDetailResponse();
  }

  return response;
}
