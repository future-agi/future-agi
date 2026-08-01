export function resolveExactSpanDetail(spanResult, spanId) {
  if (!spanId) {
    return {
      detail: null,
      error:
        "The selected span row has no span ID. Choose another span before mapping variables.",
    };
  }

  const detail = spanResult?.observation_span || spanResult || null;
  const resolvedId = detail?.id || detail?.span_id;
  if (!resolvedId || String(resolvedId) !== String(spanId)) {
    return {
      detail: null,
      error:
        "The selected span is no longer available. Choose another span before mapping variables.",
    };
  }

  return { detail, error: null };
}

export async function fetchExactSpanPreview({
  spanId,
  httpGet,
  getObservationSpanUrl,
}) {
  const { data } = await httpGet(getObservationSpanUrl(spanId), {
    params: { preview: true },
  });
  return resolveExactSpanDetail(data?.result, spanId);
}

export const buildTracingPreviewListParams = ({
  selectedProjectId,
  effectiveFilters,
}) => ({
  project_id: selectedProjectId,
  page_number: 0,
  page_size: 50,
  filters: JSON.stringify(effectiveFilters || []),
  preview: true,
});

export const isTracingListQueryDegraded = (metadata = {}) =>
  metadata.query_complete === false ||
  metadata.query_status === "degraded" ||
  metadata.query_error_code === "read_budget_exceeded";

const isRecord = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export async function fetchTracingPreviewList({ httpGet, endpoint, params }) {
  try {
    const { data } = await httpGet(endpoint, { params });
    const result = data?.result;
    if (
      !isRecord(result) ||
      !Array.isArray(result.config) ||
      !Array.isArray(result.table) ||
      !isRecord(result.metadata) ||
      typeof result.metadata.total_rows !== "number" ||
      !Number.isFinite(result.metadata.total_rows) ||
      result.metadata.total_rows < 0
    ) {
      throw new TypeError("Malformed tracing preview list response");
    }

    const columns = result.config;
    const tableRows = result.table;
    const metadata = result.metadata;
    const queryDegraded = isTracingListQueryDegraded(metadata);

    return {
      columns,
      rows: queryDegraded ? [] : tableRows,
      totalRows: queryDegraded ? 0 : metadata.total_rows,
      queryDegraded,
      queryUnavailable: false,
    };
  } catch {
    // Never pass transport, contract, or backend details through to the UI.
    // A failed request is not a legitimate empty result and must fail closed.
    return {
      columns: [],
      rows: [],
      totalRows: 0,
      queryDegraded: false,
      queryUnavailable: true,
    };
  }
}

export const getTracingReadyState = ({
  variables,
  mapping,
  currentRow,
  spanDetail,
  rowType,
  hasDataInjection,
  spanDetailError,
  listQueryDegraded = false,
  listQueryUnavailable = false,
}) => {
  const normalizedRowType = String(rowType || "").toLowerCase();
  const isSpanRow =
    normalizedRowType === "span" || normalizedRowType === "spans";
  const staleSpanRow = isSpanRow && Boolean(spanDetailError);
  const listQueryUnsafe = listQueryDegraded || listQueryUnavailable;
  const safeMapping = staleSpanRow || listQueryUnsafe ? {} : mapping;
  const allMapped =
    variables.length === 0 ||
    variables.every(
      (variable) =>
        safeMapping[variable] && String(safeMapping[variable]).length > 0,
    );
  const selectedSpanId = currentRow?.span_id || currentRow?.spanId;
  const resolvedSpanId = spanDetail?.id || spanDetail?.span_id;
  const hasExactSpan =
    Boolean(selectedSpanId) &&
    Boolean(resolvedSpanId) &&
    String(selectedSpanId) === String(resolvedSpanId);
  const hasResolvedRow = Boolean(currentRow) && (!isSpanRow || hasExactSpan);
  const selectedSpanMustMatch = !isSpanRow || !currentRow || hasExactSpan;

  return {
    ready:
      !listQueryUnsafe &&
      !staleSpanRow &&
      selectedSpanMustMatch &&
      (hasDataInjection || (allMapped && hasResolvedRow)),
    mapping: safeMapping,
  };
};
