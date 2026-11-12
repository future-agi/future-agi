// L1 (the capability contract): the dataset page's writes on the real backend.
// Dev has no dataset versions, so a hand edit, a row delete or a row copy
// is written at once through the endpoints today's product uses, with the
// bodies dev's serializers read:
//   POST   develops/{id}/update_cell_value/  {row_id, column_id, new_value}
//   DELETE develops/{id}/delete_row/         {row_ids, selected_all_rows}
//   POST   datasets/{id}/duplicate-rows/     {row_ids, num_copies, selected_all_rows}
//   POST   develops/clone-dataset/{id}/      {} (dev names it "Copy of <name>")
// None of them sends a DSX-only field (`pending`, `dry_run`, `scope`).
import axios, { endpoints } from "src/utils/axios";

/** The cell value as dev stores it from text: objects as JSON, null as "". */
export function cellValueText(value) {
  if (value == null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Write one cell now. Resolves with the response; rejects on a refusal. */
export function writeCellClassic({ datasetId, rowId, columnId, value }) {
  return axios.post(endpoints.develop.updateCellValue(datasetId), {
    row_id: rowId,
    column_id: columnId,
    new_value: cellValueText(value),
  });
}

/** Delete rows now (soft delete on dev; there is no restore). */
export function deleteRowsClassic({ datasetId, rowIds }) {
  return axios.delete(endpoints.develop.deleteDatasetRow(datasetId), {
    data: { row_ids: rowIds, selected_all_rows: false },
  });
}

/** Copy rows now; dev writes them at once and answers `new_row_ids`. */
export function duplicateRowsClassic({ datasetId, rowIds, copies = 1 }) {
  return axios.post(endpoints.develop.duplicateDatasetRows(datasetId), {
    row_ids: rowIds,
    num_copies: Number(copies) || 1,
    selected_all_rows: false,
  });
}

/** Copy a whole dataset now; dev names the copy "Copy of <name>". */
export function cloneDatasetClassic(datasetId) {
  return axios.post(endpoints.develop.cloneDataset(datasetId), {});
}

/**
 * The sentence of a refused write: the backend's own (a string `result`,
 * as GeneralMethods.bad_request answers), else `fallback`.
 */
export function refusalSentence(error, fallback) {
  const r = error?.result ?? error?.response?.data?.result;
  if (typeof r === "string" && r.trim()) return r.trim();
  const m = error?.response?.data?.message ?? error?.message;
  return typeof m === "string" && m.trim() && !/^Request failed/i.test(m)
    ? m.trim()
    : fallback;
}

/** `1 row` / `3 rows`. */
export const rowsWord = (n) => `${n} row${n === 1 ? "" : "s"}`;
