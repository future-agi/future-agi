import axios, { endpoints } from "src/utils/axios";
import { getDataGridFilters, getDataGridSearch } from "./useDataGridView";

// Rows behind the grid's selection. Explicit selections read the loaded row
// nodes; "select all" re-reads the filtered table so actions get real ids
// and their saved splits, page by page (HO D6: every matching row, past
// 500; the table answers at most BULK_ROW_CAP rows a page).

export const BULK_ROW_CAP = 500;

const pick = (row) => ({
  row_id: row?.row_id,
  split_ids: row?.row_meta?.split_ids ?? [],
  pending: row?.__pending ?? null,
});

/** @returns {Promise<Array<{row_id, split_ids, pending}>>} */
export async function resolveSelectedRows({
  datasetId,
  api,
  selectAll,
  toggledNodes,
}) {
  const toggled = toggledNodes ?? [];
  if (!selectAll) {
    return toggled
      .map((id) => api?.getRowNode?.(id)?.data ?? { row_id: id })
      .map(pick)
      .filter((r) => r.row_id);
  }
  const search = getDataGridSearch();
  const params = {
    page_size: BULK_ROW_CAP,
    filters: JSON.stringify(getDataGridFilters(datasetId)),
    sort: JSON.stringify([]),
    ...(search && {
      search: JSON.stringify({ key: search, type: ["text", "image", "audio"] }),
    }),
  };
  const table = [];
  const seen = new Set();
  for (let page = 0; page !== null; ) {
    const { data } = await axios.get(
      endpoints.develop.getDatasetDetail(datasetId),
      { params: { ...params, current_page_index: page } },
    );
    const rows = data?.result?.table ?? [];
    rows.forEach((r) => {
      if (seen.has(r.row_id)) return;
      seen.add(r.row_id);
      table.push(r);
    });
    page = nextPageOf(data?.result?.metadata, page, rows.length);
  }
  const excluded = new Set(toggled);
  return table.filter((r) => !excluded.has(r.row_id)).map(pick);
}

/**
 * The next page to read, or null: the table's `has_more` /
 * `next_page_index`, else a full page means there may be more.
 */
export function nextPageOf(meta, page, got) {
  if (!got) return null;
  if (meta && typeof meta.has_more === "boolean")
    return meta.has_more ? meta.next_page_index ?? page + 1 : null;
  if (Number.isFinite(meta?.total_pages))
    return page + 1 < meta.total_pages ? page + 1 : null;
  return got >= BULK_ROW_CAP ? page + 1 : null;
}

/** Number of selected rows from AG Grid's server-side selection state. */
export function selectedCount({ selectAll, toggledNodes, total }) {
  return selectAll
    ? Math.max(0, (total ?? 0) - (toggledNodes?.length ?? 0))
    : toggledNodes?.length ?? 0;
}
