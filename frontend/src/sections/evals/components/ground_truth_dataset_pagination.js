import {
  formatRuntimeSeconds,
  GROUND_TRUTH_DATASET_PAGE_SIZE as CONFIGURED_GROUND_TRUTH_DATASET_PAGE_SIZE,
  INTERACTIVE_REQUEST_TIMEOUT_MS,
} from "src/config/runtime_limits";

export const GROUND_TRUTH_DATASET_PAGE_SIZE =
  CONFIGURED_GROUND_TRUTH_DATASET_PAGE_SIZE;
export const GROUND_TRUTH_DATASET_ACTION_TIMEOUT_MS =
  INTERACTIVE_REQUEST_TIMEOUT_MS;

const paginationError = (code, message) => {
  const error = new Error(message);
  error.code = code;
  return error;
};

export const createEmptyGroundTruthDatasetRead = () => ({
  rows: [],
  rowIds: [],
  columns: null,
  columnIds: null,
  datasetName: null,
  totalRows: null,
  pageSize: GROUND_TRUTH_DATASET_PAGE_SIZE,
  nextPageIndex: 0,
  nextCursor: null,
  hasMore: true,
  complete: false,
});

const requireNonNegativeInteger = (value, field) => {
  if (!Number.isInteger(value) || value < 0) {
    throw paginationError(
      "ground_truth_dataset_invalid_page",
      `Dataset response has an invalid ${field}.`,
    );
  }
  return value;
};

const pageColumns = (result) => {
  if (!Array.isArray(result?.column_config)) {
    throw paginationError(
      "ground_truth_dataset_invalid_page",
      "Dataset response is missing its column inventory.",
    );
  }
  return result.column_config.map((column) => ({
    id: String(column?.id || ""),
    name: String(column?.name || ""),
  }));
};

const sameStrings = (left, right) =>
  left.length === right.length &&
  left.every((value, index) => value === right[index]);

/**
 * Append one exact, sequential server page.
 *
 * The importer deliberately fails closed on changed totals/columns, duplicate
 * rows, malformed continuation metadata, or a non-exact server result.  A
 * partial prefix can stay visible and be retried, but it can never be uploaded
 * as a successful ground-truth dataset.
 */
export const appendGroundTruthDatasetPage = (
  previous,
  result,
  requestedPageIndex,
) => {
  const current = previous || createEmptyGroundTruthDatasetRead();
  const metadata = result?.metadata;
  const pageRows = result?.table;

  if (!metadata || !Array.isArray(pageRows)) {
    throw paginationError(
      "ground_truth_dataset_invalid_page",
      "Dataset response is missing rows or pagination metadata.",
    );
  }
  if (
    metadata.is_exact !== true ||
    metadata.snapshot_bound !== true ||
    metadata.error_messages?.length > 0
  ) {
    throw paginationError(
      "ground_truth_dataset_inexact_page",
      "The dataset page was not read exactly. Retry before importing.",
    );
  }

  const pageIndex = requireNonNegativeInteger(
    metadata.current_page_index,
    "current_page_index",
  );
  const totalRows = requireNonNegativeInteger(
    metadata.total_rows,
    "total_rows",
  );
  const pageSize = requireNonNegativeInteger(metadata.page_size, "page_size");
  const totalPages = requireNonNegativeInteger(
    metadata.total_pages,
    "total_pages",
  );

  if (
    pageSize !== GROUND_TRUTH_DATASET_PAGE_SIZE ||
    pageIndex !== requestedPageIndex ||
    pageIndex !== current.nextPageIndex ||
    current.rows.length !== pageIndex * pageSize
  ) {
    throw paginationError(
      "ground_truth_dataset_cursor_mismatch",
      "Dataset continuation did not match the rows already loaded.",
    );
  }

  const expectedPages = totalRows === 0 ? 0 : Math.ceil(totalRows / pageSize);
  const expectedPageRows = Math.min(
    pageSize,
    Math.max(0, totalRows - pageIndex * pageSize),
  );
  if (totalPages !== expectedPages || pageRows.length !== expectedPageRows) {
    throw paginationError(
      "ground_truth_dataset_count_mismatch",
      "Dataset row counts changed while the import was loading.",
    );
  }
  if (current.totalRows !== null && current.totalRows !== totalRows) {
    throw paginationError(
      "ground_truth_dataset_count_mismatch",
      "Dataset row counts changed while the import was loading.",
    );
  }

  const columns = pageColumns(result);
  const columnIds = columns.map((column) => `${column.id}\0${column.name}`);
  if (columns.some((column) => !column.id || !column.name.trim())) {
    throw paginationError(
      "ground_truth_dataset_invalid_page",
      "Dataset response contains an invalid column identifier or name.",
    );
  }
  const normalizedColumnNames = columns.map((column) => column.name.trim());
  if (new Set(normalizedColumnNames).size !== normalizedColumnNames.length) {
    throw paginationError(
      "ground_truth_dataset_duplicate_column",
      "Dataset column names must be unique before importing.",
    );
  }
  if (current.columnIds && !sameStrings(current.columnIds, columnIds)) {
    throw paginationError(
      "ground_truth_dataset_columns_changed",
      "Dataset columns changed while the import was loading.",
    );
  }

  const datasetName = String(metadata.dataset_name || "");
  if (current.datasetName !== null && current.datasetName !== datasetName) {
    throw paginationError(
      "ground_truth_dataset_identity_changed",
      "Dataset identity changed while the import was loading.",
    );
  }

  const knownRowIds = new Set(current.rowIds);
  const incomingRowIds = [];
  for (const row of pageRows) {
    const rowId = typeof row?.row_id === "string" ? row.row_id : "";
    if (!rowId || knownRowIds.has(rowId)) {
      throw paginationError(
        "ground_truth_dataset_duplicate_row",
        "Dataset continuation returned a missing or duplicate row.",
      );
    }
    knownRowIds.add(rowId);
    incomingRowIds.push(rowId);
  }

  const loadedRows = current.rows.length + pageRows.length;
  const expectedHasMore = loadedRows < totalRows;
  const expectedNextPage = expectedHasMore ? pageIndex + 1 : null;
  const nextCursor = metadata.next_cursor;
  if (
    metadata.has_more !== expectedHasMore ||
    metadata.next_page_index !== expectedNextPage ||
    (expectedHasMore &&
      (typeof nextCursor !== "string" || nextCursor.length === 0)) ||
    (expectedHasMore &&
      current.nextCursor !== null &&
      nextCursor === current.nextCursor) ||
    (!expectedHasMore && nextCursor !== null)
  ) {
    throw paginationError(
      "ground_truth_dataset_cursor_mismatch",
      "Dataset continuation metadata is inconsistent.",
    );
  }

  return {
    rows: [...current.rows, ...pageRows],
    rowIds: [...current.rowIds, ...incomingRowIds],
    columns,
    columnIds,
    datasetName,
    totalRows,
    pageSize,
    nextPageIndex: expectedNextPage,
    nextCursor,
    hasMore: expectedHasMore,
    complete: !expectedHasMore && loadedRows === totalRows,
  };
};

/**
 * One user action performs one bounded page request.  Promise.race keeps the
 * wall honest even when a transport adapter ignores AbortSignal; late results
 * are never appended to the visible exact prefix.
 */
export const readNextGroundTruthDatasetPage = async ({
  previous,
  requestPage,
  timeoutMs = GROUND_TRUTH_DATASET_ACTION_TIMEOUT_MS,
}) => {
  if (previous?.complete) return previous;
  const current = previous || createEmptyGroundTruthDatasetRead();
  const pageIndex = current.nextPageIndex;
  if (!Number.isInteger(pageIndex)) {
    throw paginationError(
      "ground_truth_dataset_cursor_mismatch",
      "Dataset continuation is unavailable.",
    );
  }

  const controller = new AbortController();
  let timer;
  const timeoutError = paginationError(
    "ground_truth_dataset_timeout",
    `Dataset rows did not load within ${formatRuntimeSeconds(
      timeoutMs,
    )} seconds. Retry Load more.`,
  );
  const deadline = new Promise((_, reject) => {
    timer = globalThis.setTimeout(() => {
      controller.abort(timeoutError);
      reject(timeoutError);
    }, timeoutMs);
  });

  try {
    const response = await Promise.race([
      requestPage({
        pageIndex,
        pageSize: GROUND_TRUTH_DATASET_PAGE_SIZE,
        cursor: current.nextCursor,
        signal: controller.signal,
        timeout: timeoutMs,
      }),
      deadline,
    ]);
    return appendGroundTruthDatasetPage(
      current,
      response?.data?.result,
      pageIndex,
    );
  } finally {
    globalThis.clearTimeout(timer);
  }
};
