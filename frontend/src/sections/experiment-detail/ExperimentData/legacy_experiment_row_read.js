import {
  DATASET_ROW_ADJACENCY_MAX_ROWS,
  INTERACTIVE_REQUEST_TIMEOUT_MS,
} from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

export const LEGACY_EXPERIMENT_ROW_REQUEST_TIMEOUT_MS =
  INTERACTIVE_REQUEST_TIMEOUT_MS;

const invalidLegacyExperimentRowResponse = () => {
  const error = new Error(
    "Experiment row continuation returned an invalid response",
  );
  error.code = "legacy_experiment_row_invalid_response";
  return error;
};

const isRecord = (value) =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const hasUniqueIds = (ids) =>
  ids.every((id) => typeof id === "string" && id.length > 0) &&
  new Set(ids).size === ids.length;

/** Read the legacy point-detail alias without trusting its old camel-case key. */
export async function readLegacyExperimentRow(
  requestRow,
  requestedRowId,
  upstreamSignal,
) {
  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestRow({
        signal,
        timeout: LEGACY_EXPERIMENT_ROW_REQUEST_TIMEOUT_MS,
      }),
    {
      timeoutMs: LEGACY_EXPERIMENT_ROW_REQUEST_TIMEOUT_MS,
      signal: upstreamSignal,
    },
  );
  const body = response?.data;
  const result = body?.result;
  const table = result?.table;
  const nextRowIds = result?.next_row_ids;
  const currentRowId = table?.[0]?.row_id;

  if (
    body?.status !== true ||
    !isRecord(result) ||
    !Array.isArray(result.column_config) ||
    !Array.isArray(table) ||
    table.length !== 1 ||
    String(currentRowId) !== String(requestedRowId) ||
    !Array.isArray(nextRowIds) ||
    nextRowIds.length > DATASET_ROW_ADJACENCY_MAX_ROWS ||
    !hasUniqueIds(nextRowIds) ||
    nextRowIds.some((id) => String(id) === String(requestedRowId))
  ) {
    throw invalidLegacyExperimentRowResponse();
  }

  return nextRowIds;
}
