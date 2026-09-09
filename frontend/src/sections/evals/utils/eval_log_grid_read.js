import {
  INTERACTIVE_MAX_PAGE_SIZE,
} from "src/config/runtime_limits";
import { awaitAggregationRequestWithDeadline } from "src/utils/queryReadState";

// Axios uses zero to disable its transport timeout for an exact read.
export const EVAL_LOG_GRID_REQUEST_TIMEOUT_MS = 0;

const invalidPage = () => {
  const error = new Error("Evaluation logs returned an invalid page");
  error.code = "eval_log_invalid_page";
  return error;
};

const normalizeRow = (row) => {
  const rowId = row?.row_id ?? row?.rowId;
  const logId = row?.log_id ?? row?.logId;
  if (typeof rowId !== "string" || !rowId || !logId) throw invalidPage();
  return { ...row, rowId, logId };
};

/**
 * Read one exact server-side grid page until completion or cancellation.
 * Rejections remain rejections so AG Grid callers can invoke `params.fail()`
 * and retain already-rendered cache blocks.
 */
export async function readEvalLogGridPage(
  requestPage,
  { currentPageIndex, pageSize, signal: cancellationSignal } = {},
) {
  const response = await awaitAggregationRequestWithDeadline(
    (signal) =>
      requestPage({
        signal,
        timeout: EVAL_LOG_GRID_REQUEST_TIMEOUT_MS,
      }),
    { timeoutMs: Infinity, signal: cancellationSignal },
  );
  const result = response?.data?.result;
  const metadata = result?.metadata;
  if (
    !result ||
    !Array.isArray(result.column_config) ||
    !Array.isArray(result.table) ||
    !metadata ||
    !Number.isSafeInteger(metadata.total_rows) ||
    metadata.total_rows < 0 ||
    !Number.isSafeInteger(metadata.total_pages) ||
    metadata.total_pages < 0 ||
    !Number.isSafeInteger(metadata.current_page_index) ||
    metadata.current_page_index < 0 ||
    !Number.isSafeInteger(metadata.page_size) ||
    metadata.page_size < 1 ||
    metadata.page_size > INTERACTIVE_MAX_PAGE_SIZE ||
    metadata.query_complete !== true ||
    metadata.query_status !== "complete" ||
    metadata.query_sampled !== false ||
    result.table.length > metadata.page_size ||
    result.table.length > metadata.total_rows ||
    metadata.total_pages !==
      Math.ceil(metadata.total_rows / metadata.page_size) ||
    (Number.isSafeInteger(currentPageIndex) &&
      metadata.current_page_index !== currentPageIndex) ||
    (Number.isSafeInteger(pageSize) && metadata.page_size !== pageSize)
  ) {
    throw invalidPage();
  }

  const rows = result.table.map(normalizeRow);
  if (new Set(rows.map((row) => row.rowId)).size !== rows.length) {
    throw invalidPage();
  }
  return {
    columns: result.column_config,
    rows,
    totalRows: metadata.total_rows,
  };
}

/** Own pending requests for one filter/evaluation datasource generation. */
export function createEvalLogReadScope() {
  let generation = 0;
  const controllers = new Set();
  return {
    generation: () => generation,
    hasPendingReads: () => controllers.size > 0,
    isCurrent: (value) => value === generation,
    cancel() {
      generation += 1;
      for (const controller of controllers) controller.abort();
      controllers.clear();
    },
    async readPage(request, options) {
      const controller = new AbortController();
      controllers.add(controller);
      try {
        const page = await readEvalLogGridPage(request, {
          ...options,
          signal: controller.signal,
        });
        if (controller.signal.aborted) throw controller.signal.reason;
        return page;
      } finally {
        controllers.delete(controller);
      }
    },
  };
}
