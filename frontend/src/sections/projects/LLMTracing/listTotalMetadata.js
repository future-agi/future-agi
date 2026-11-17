import {
  getQueryReadMessage,
  getQueryReadState,
} from "src/utils/queryReadState";

const normalizeRowCount = (value) => {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.floor(count) : 0;
};

/**
 * List rows returned by the bounded selectors are individually proven exact.
 * A sampled/lower-bound aggregate marker therefore must not turn those rows
 * into an "incomplete" UI state or produce a sampling banner. Keep a retry
 * message only for a genuinely degraded empty page or a transport failure.
 */
export const getListReadMessage = (payload, { isError = false } = {}) => {
  const readState = getQueryReadState(payload, { isError });
  const rows = payload?.result?.table ?? payload?.table;
  if (Array.isArray(rows) && rows.length > 0) return null;
  if (readState === "sampled") return null;
  return getQueryReadMessage(readState);
};

/**
 * Keep an API lower bound separate from an exact row count. Consumers that
 * require an exact total should only use totalRowCount.
 */
export const getListTotalState = (metadata = {}) => {
  const totalRowCountIsLowerBound =
    metadata?.total_rows_is_lower_bound === true;
  const reportedTotal = normalizeRowCount(metadata?.total_rows);

  return {
    totalRowCount: totalRowCountIsLowerBound ? null : reportedTotal,
    totalRowCountLowerBound: totalRowCountIsLowerBound ? reportedTotal : null,
    totalRowCountIsLowerBound,
  };
};

/**
 * In server-side select-all mode toggledNodes contains exclusions, so
 * subtracting them from a reported lower bound remains a lower bound.
 */
export const getSelectionCountState = ({
  selectAll,
  toggledNodes,
  totalRowCount,
  totalRowCountLowerBound,
  totalRowCountIsLowerBound,
}) => {
  const toggledCount = Array.isArray(toggledNodes) ? toggledNodes.length : 0;
  if (!selectAll) {
    return { count: toggledCount, isLowerBound: false };
  }

  const reportedTotal = totalRowCountIsLowerBound
    ? totalRowCountLowerBound
    : totalRowCount;

  return {
    count: Math.max(
      normalizeRowCount(reportedTotal) - toggledCount,
      totalRowCountIsLowerBound ? 0 : 1,
    ),
    isLowerBound: totalRowCountIsLowerBound === true,
  };
};

export const formatSelectionCount = ({ count, isLowerBound }) =>
  `${isLowerBound ? "≥" : ""}${normalizeRowCount(count).toLocaleString()}`;
