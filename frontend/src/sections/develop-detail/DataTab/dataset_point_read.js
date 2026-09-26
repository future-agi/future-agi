import {
  DATASET_ROW_ADJACENCY_MAX_ROWS,
  INTERACTIVE_REQUEST_TIMEOUT_MS,
} from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

export const DATASET_POINT_READ_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

const datasetPointReadError = (code, message) => {
  const error = new Error(message);
  error.code = code;
  return error;
};

const invalidAdjacencyResponse = () =>
  datasetPointReadError(
    "dataset_row_adjacency_invalid_response",
    "Dataset row adjacency returned an invalid response",
  );

const invalidCellResponse = () =>
  datasetPointReadError(
    "dataset_cell_data_invalid_response",
    "Dataset cell data returned an invalid response",
  );

const isRecord = (value) =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const isNonEmptyString = (value) =>
  typeof value === "string" && value.length > 0;

const hasUniqueIds = (ids) =>
  ids.every(isNonEmptyString) && new Set(ids).size === ids.length;

/** One navigation click owns this wall even when it needs two point reads. */
export function runDatasetPointReadAction(action, upstreamSignal) {
  return awaitAggregationRequestWithDeadline(action, {
    timeoutMs: DATASET_POINT_READ_TIMEOUT_MS,
    signal: upstreamSignal,
  });
}

export async function readDatasetRowAdjacency(
  requestRow,
  payload,
  upstreamSignal,
) {
  const requestedRowId = payload?.row_id;
  if (!isNonEmptyString(requestedRowId)) throw invalidAdjacencyResponse();

  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestRow({
        signal,
        timeout: DATASET_POINT_READ_TIMEOUT_MS,
      }),
    { timeoutMs: DATASET_POINT_READ_TIMEOUT_MS, signal: upstreamSignal },
  );
  const body = response?.data;
  const result = body?.result;
  const current = result?.current;
  const nextRowIds = result?.next?.row_id;

  if (
    body?.status !== true ||
    !isRecord(result) ||
    !isRecord(current) ||
    String(current.row_id) !== requestedRowId ||
    !Array.isArray(nextRowIds) ||
    nextRowIds.length > DATASET_ROW_ADJACENCY_MAX_ROWS ||
    !hasUniqueIds(nextRowIds) ||
    nextRowIds.includes(requestedRowId)
  ) {
    throw invalidAdjacencyResponse();
  }

  return { current, nextRowIds };
}

export async function readDatasetCellRow(
  requestCells,
  payload,
  upstreamSignal,
) {
  const requestedRowIds = payload?.row_ids;
  if (
    !Array.isArray(requestedRowIds) ||
    requestedRowIds.length !== 1 ||
    !isNonEmptyString(requestedRowIds[0])
  ) {
    throw invalidCellResponse();
  }

  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestCells({
        signal,
        timeout: DATASET_POINT_READ_TIMEOUT_MS,
      }),
    { timeoutMs: DATASET_POINT_READ_TIMEOUT_MS, signal: upstreamSignal },
  );
  const body = response?.data;
  const result = body?.result;
  const requestedRowId = requestedRowIds[0];
  const rowData = result?.[requestedRowId];

  if (
    body?.status !== true ||
    !isRecord(result) ||
    !isRecord(rowData) ||
    (rowData.row_id !== undefined && String(rowData.row_id) !== requestedRowId)
  ) {
    throw invalidCellResponse();
  }

  return { row_id: requestedRowId, ...rowData };
}
