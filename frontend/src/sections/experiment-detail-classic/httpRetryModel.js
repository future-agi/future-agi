// SRC S1b: the classic experiment page's HTTP actions (pure). Retry cell and
// Retry failed rows re-send only the chosen rows; the server gives each
// explicit retry a new attempt. The page asks first, every time (see
// httpResendCopy.js), acknowledges the possible side effects on the request,
// and never re-sends on its own. Add new rows sends exactly the rows the
// server's preview names as new.

/** The most rows an HTTP experiment holds (the server's snapshot limit). */
export const HTTP_ROW_LIMIT = 10000;

/** The saved HTTP sources of an experiment detail read: `{id, name, url}`. */
export function httpSourcesOf(detail) {
  return (detail?.http_configs ?? [])
    .filter((config) => config?.id && config?.http)
    .map((config) => ({
      id: String(config.id),
      name: config.name || "HTTP endpoint",
      url: config.http?.url ?? "",
    }));
}

/**
 * The HTTP source whose output a grid column holds, or null. Evaluation and
 * prompt columns are not HTTP re-sends.
 */
export function httpSourceOfColumn(columnId, columns = [], sources = []) {
  if (!columnId || !sources.length) return null;
  const column = (columns ?? []).find((c) => String(c?.id) === String(columnId));
  const origin = column?.origin_type ?? column?.originType;
  const sourceId = column?.source_id ?? column?.sourceId;
  if (!column || String(origin).toLowerCase() !== "experiment" || !sourceId)
    return null;
  return sources.find((s) => s.id === String(sourceId)) ?? null;
}

const parsed = (raw) => {
  if (!raw) return null;
  if (typeof raw === "object") return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

/**
 * How the server's reason starts when an HTTP attempt did not finish (its
 * unknown_outcome settle and cleanup both write it). The grid's rows read
 * keeps only `value_infos.reason` and `metadata.reason`, not the error code.
 */
export const HTTP_UNFINISHED_REASON = "The HTTP attempt did not finish.";

/**
 * Whether a cell's last attempt ended without a known outcome. Reads the
 * grid's cell (`{cell_value, status, metadata: {reason}, value_infos:
 * {reason}}`) and a full stored `value_infos` with `error.code`.
 */
export function cellOutcomeUnknown(cell) {
  const info = parsed(cell?.value_infos ?? cell?.valueInfos);
  if (info?.error?.code === "unknown_outcome") return true;
  const reason = info?.reason ?? cell?.metadata?.reason;
  return (
    typeof reason === "string" && reason.startsWith(HTTP_UNFINISHED_REASON)
  );
}

/** rerun-cells body of Retry cell: this one HTTP output cell. */
export const retryCellBody = ({ columnId, rowId }) => ({
  cells: [{ column_id: columnId, row_id: rowId }],
  acknowledge_http_side_effects: true,
});

/**
 * Whether the experiment ran on selected or sampled rows (the detail read's
 * `row_ids`/`sample`, or the same keys of its `run_scope`).
 */
export function ranOnSelectedRows(detail) {
  const scope = detail?.run_scope ?? {};
  return Boolean(
    detail?.row_ids?.length ||
      detail?.sample ||
      scope?.row_ids?.length ||
      scope?.sample,
  );
}

const isCount = (n) => Number.isSafeInteger(n) && n >= 0;

/**
 * Add new rows: what the server's read-only preview says will be sent, or
 * null when the answer is not a preview. The server compares row identities
 * (the dataset rows read against the rows the experiment already holds), so
 * `rowIds` are exactly the new rows, to submit unchanged, and `requests` is
 * how many requests they send. `overLimit` is true when the experiment would
 * then hold more rows than it can.
 */
export function addRowsPreviewOf(result) {
  const ids = result?.new_row_ids;
  const { request_count: requests, held_count: held, limit } = result ?? {};
  if (
    result?.preview !== true ||
    !Array.isArray(ids) ||
    !ids.every((id) => typeof id === "string" && id) ||
    !isCount(requests) ||
    !isCount(held) ||
    !isCount(limit)
  )
    return null;
  return { rowIds: ids, requests, overLimit: held + ids.length > limit };
}

/** rerun-cells body of Retry failed rows: the failed rows of these sources. */
export const retryFailedRowsBody = (sources = []) => ({
  source_ids: sources.map((s) => s.id),
  failed_only: true,
  acknowledge_http_side_effects: true,
});
