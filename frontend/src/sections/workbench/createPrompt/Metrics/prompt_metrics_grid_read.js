import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

export const PROMPT_METRICS_REQUEST_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

const invalidResponse = () => {
  const error = new Error("Prompt metrics returned an invalid page");
  error.code = "prompt_metrics_invalid_page";
  return error;
};

/**
 * Read one prompt-metrics grid page under an independent transport wall.
 * Malformed and late responses fail closed; callers must retain any rows that
 * were already rendered and report the server-side block as failed.
 */
export async function readPromptMetricsGridPage(requestPage) {
  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestPage({
        signal,
        timeout: PROMPT_METRICS_REQUEST_TIMEOUT_MS,
      }),
    { timeoutMs: PROMPT_METRICS_REQUEST_TIMEOUT_MS },
  );
  const result = response?.data?.result;
  const totalRows = result?.metadata?.total_rows;
  if (
    !result ||
    !Array.isArray(result.config) ||
    !Array.isArray(result.table) ||
    !Number.isSafeInteger(totalRows) ||
    totalRows < 0 ||
    result.table.length > totalRows
  ) {
    throw invalidResponse();
  }
  return {
    columns: result.config,
    rowData: result.table,
    totalRows,
  };
}
