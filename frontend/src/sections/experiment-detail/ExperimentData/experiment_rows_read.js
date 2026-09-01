import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

export const EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS =
  INTERACTIVE_REQUEST_TIMEOUT_MS;

const experimentRowsError = (code, message) => {
  const error = new Error(message);
  error.code = code;
  return error;
};

const invalidExperimentRows = () =>
  experimentRowsError(
    "experiment_rows_invalid_response",
    "Experiment rows returned an invalid response",
  );

const boundedTimeout = (timeoutMs) => {
  const numericTimeout = Number(timeoutMs);
  const value = Number.isFinite(numericTimeout)
    ? Math.min(numericTimeout, EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS)
    : EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS;
  if (value <= 0) {
    throw experimentRowsError(
      "aggregation_request_timeout",
      "Experiment rows request did not complete",
    );
  }
  return value;
};

/** One visible refresh (columns plus every cached block) shares this budget. */
export function createExperimentRowsActionBudget({ now = Date.now } = {}) {
  const startedAt = now();
  return {
    remainingMs() {
      const remaining =
        EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS - Math.max(now() - startedAt, 0);
      return boundedTimeout(remaining);
    },
  };
}

export async function readExperimentRowsPage(
  requestPage,
  upstreamSignal,
  { pageSize, timeoutMs = EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS } = {},
) {
  const requestTimeout = boundedTimeout(timeoutMs);
  const response = await awaitAggregationRequestWithDeadline(
    (signal) => requestPage({ signal, timeout: requestTimeout }),
    { timeoutMs: requestTimeout, signal: upstreamSignal },
  );
  const body = response?.data;
  const result = body?.result;
  const rows = result?.table;
  const metadata = result?.metadata;
  const totalRows = metadata?.total_rows;
  const totalPages = metadata?.total_pages;
  const rowIds = Array.isArray(rows) ? rows.map((row) => row?.row_id) : [];
  const hasUniqueRowIds =
    rowIds.every((id) => id !== null && id !== undefined) &&
    new Set(rowIds.map(String)).size === rowIds.length;
  const expectedTotalPages =
    Number.isSafeInteger(totalRows) &&
    Number.isSafeInteger(pageSize) &&
    pageSize > 0
      ? Math.ceil(totalRows / pageSize)
      : null;

  if (
    body?.status !== true ||
    !result ||
    !Array.isArray(result.column_config) ||
    !Array.isArray(rows) ||
    !hasUniqueRowIds ||
    !Number.isSafeInteger(totalRows) ||
    totalRows < 0 ||
    !Number.isSafeInteger(totalPages) ||
    totalPages !== expectedTotalPages ||
    rows.length > pageSize ||
    rows.length > totalRows ||
    typeof result.status !== "string" ||
    result.status.length === 0
  ) {
    throw invalidExperimentRows();
  }

  return result;
}

export async function readExperimentColumnConfig(
  requestColumns,
  upstreamSignal,
) {
  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestColumns({
        signal,
        timeout: EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS,
      }),
    {
      timeoutMs: EXPERIMENT_ROWS_REQUEST_TIMEOUT_MS,
      signal: upstreamSignal,
    },
  );
  const body = response?.data;
  const result = body?.result;
  if (
    body?.status !== true ||
    !result ||
    !Array.isArray(result.column_config) ||
    typeof result.status !== "string" ||
    result.status.length === 0
  ) {
    throw invalidExperimentRows();
  }
  return body;
}
