import {
  EVAL_METRIC_MAX_WINDOW_DAYS,
  INTERACTIVE_REQUEST_TIMEOUT_MS,
} from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

export const EVAL_METRICS_REQUEST_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

const invalidEvalMetrics = () => {
  const error = new Error("Evaluation metrics returned an invalid response");
  error.code = "eval_metrics_invalid_response";
  return error;
};

const isGraphPoint = (point) =>
  point &&
  typeof point === "object" &&
  typeof point.timestamp === "string" &&
  point.timestamp.length > 0 &&
  typeof point.value === "number" &&
  Number.isFinite(point.value);

/** Read one exact eval-metrics graph without letting late/malformed data chart. */
export async function readEvalMetrics(requestMetrics, upstreamSignal) {
  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestMetrics({ signal, timeout: EVAL_METRICS_REQUEST_TIMEOUT_MS }),
    {
      timeoutMs: EVAL_METRICS_REQUEST_TIMEOUT_MS,
      signal: upstreamSignal,
    },
  );
  const body = response?.data;
  const result = body?.result;
  const count = result?.api_call_count;
  const average = result?.average;
  const metadata = result?.metadata;
  const countGraph = count?.count_graph_data;
  const averageGraph = average?.avg_graph_data;
  const timestampsMatch =
    Array.isArray(countGraph) &&
    Array.isArray(averageGraph) &&
    countGraph.length === averageGraph.length &&
    countGraph.every(
      (point, index) => point.timestamp === averageGraph[index]?.timestamp,
    );

  if (
    body?.status !== true ||
    !result ||
    typeof result.base_eval_template_id !== "string" ||
    !Number.isSafeInteger(count?.api_call_count) ||
    count.api_call_count < 0 ||
    !Array.isArray(countGraph) ||
    countGraph.some((point) => !isGraphPoint(point)) ||
    typeof average?.average !== "number" ||
    !Number.isFinite(average.average) ||
    !Array.isArray(averageGraph) ||
    averageGraph.some((point) => !isGraphPoint(point)) ||
    !timestampsMatch ||
    !Number.isSafeInteger(metadata?.bucket_count) ||
    metadata.bucket_count !== countGraph.length ||
    metadata.query_complete !== true ||
    metadata.query_sampled !== false ||
    metadata.has_more !== false ||
    !Number.isSafeInteger(metadata.max_window_days) ||
    metadata.max_window_days < 1 ||
    metadata.max_window_days > EVAL_METRIC_MAX_WINDOW_DAYS
  ) {
    throw invalidEvalMetrics();
  }

  return body;
}
