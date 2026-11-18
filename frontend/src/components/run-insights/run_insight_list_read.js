import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

export const RUN_INSIGHT_LIST_REQUEST_TIMEOUT_MS =
  INTERACTIVE_REQUEST_TIMEOUT_MS;

/** Bound one Run Insights list request even when the transport stalls. */
export function readRunInsightListPage(requestPage) {
  return awaitAggregationRequestWithDeadline(
    (signal) =>
      requestPage({
        signal,
        timeout: RUN_INSIGHT_LIST_REQUEST_TIMEOUT_MS,
      }),
    { timeoutMs: RUN_INSIGHT_LIST_REQUEST_TIMEOUT_MS },
  );
}
