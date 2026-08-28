import { OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS } from "src/config/runtime_limits";

const truncatePreview = (value) => {
  if (value.length <= OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS) return value;
  return `${value.slice(0, OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS - 1)}…`;
};

const boundCellValue = (value) => {
  if (typeof value === "string") return truncatePreview(value);
  if (!Array.isArray(value) && (value === null || typeof value !== "object")) {
    return value;
  }
  let rendered;
  try {
    rendered = JSON.stringify(value);
  } catch {
    rendered = String(value);
  }
  return rendered.length > OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS
    ? truncatePreview(rendered)
    : value;
};

/**
 * Keep list rows bounded before cursor overflow and AG Grid cache retain them.
 * Full values remain available from the trace/span detail endpoints.
 */
export const boundObserveListRow = (row) => {
  if (!row || typeof row !== "object") return row;
  return Object.fromEntries(
    Object.entries(row).map(([key, value]) => [key, boundCellValue(value)]),
  );
};
