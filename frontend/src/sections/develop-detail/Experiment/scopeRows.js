// Reading the rows a run will use. The dataset table clamps every page, so a
// single request never stands for a whole scope: read page after page.
import { useQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { pinnedVersionOf, scopeRequestOf } from "./useVersionScope";

/** The largest page the dataset table returns. */
export const TABLE_PAGE_ROWS = 500;
/** How many rows Review reads to preview a sample or split scope. */
export const PREVIEW_ROW_LIMIT = 5000;

/**
 * Every row a table request returns, page by page, up to `limit` rows.
 * `complete` is false only when the limit stopped the read early.
 * @returns {Promise<{rows: object[], complete: boolean, total: number|null}>}
 */
export async function readAllTableRows(
  url,
  params = {},
  { limit = Infinity, get = axios.get } = {},
) {
  const rows = [];
  let total = null;
  for (let page = 0; ; page += 1) {
    // eslint-disable-next-line no-await-in-loop
    const res = await get(url, {
      params: {
        ...params,
        page_size: TABLE_PAGE_ROWS,
        current_page_index: page,
      },
    });
    const result = res?.data?.result ?? {};
    const table = result.table ?? [];
    const reported = Number(result.metadata?.total_rows);
    if (result.metadata?.total_rows != null && Number.isFinite(reported))
      total = reported;
    rows.push(...table);
    if (!table.length || (total != null && rows.length >= total))
      return { rows, complete: true, total };
    if (rows.length >= limit) return { rows, complete: false, total };
  }
}

/**
 * The rows in scope of the pinned version (splits, or every row for a
 * sample), for Review's warnings. A scope larger than the preview limit
 * comes back incomplete, and Review then uses the dataset's own column
 * statistics instead.
 */
export function useScopePreviewRows({
  datasetId,
  versions,
  versionId,
  splitIds = [],
  all = false,
}) {
  const pinned = pinnedVersionOf(versions, versionId);
  return useQuery({
    queryKey: [
      "experiment-create",
      "review-scope-rows",
      datasetId,
      pinned.versionId ?? "latest",
      [...splitIds].sort().join(","),
      all ? "all" : "splits",
    ],
    queryFn: () => {
      const { url, params } = scopeRequestOf({ datasetId, pinned, splitIds });
      return readAllTableRows(url, params, { limit: PREVIEW_ROW_LIMIT });
    },
    enabled: Boolean(datasetId) && (splitIds.length > 0 || all),
    staleTime: 30_000,
  });
}
