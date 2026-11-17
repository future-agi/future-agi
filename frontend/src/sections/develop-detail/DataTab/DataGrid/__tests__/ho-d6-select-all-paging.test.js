// HO D6 (W5 C3 data half): "select all" reads every matching row page by
// page (past 500), and Download (n) exports exactly the selection.
import { beforeEach, describe, expect, it, vi } from "vitest";

const pages = [];
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(async (_url, { params }) => {
      pages.push(params.current_page_index);
      const total = 1203;
      const size = params.page_size;
      const start = params.current_page_index * size;
      const ids = Array.from(
        { length: Math.max(0, Math.min(size, total - start)) },
        (_, i) => `r${start + i}`,
      );
      const hasMore = start + size < total;
      return {
        data: {
          result: {
            table: ids.map((row_id) => ({
              row_id,
              row_meta: { split_ids: [] },
            })),
            metadata: {
              has_more: hasMore,
              next_page_index: hasMore ? params.current_page_index + 1 : null,
              total_pages: Math.ceil(total / size),
            },
          },
        },
      };
    }),
  },
  endpoints: {
    develop: { getDatasetDetail: (id) => `/model-hub/develops/${id}/table/` },
  },
}));
vi.mock("../useDataGridView", () => ({
  getDataGridFilters: () => [],
  getDataGridSearch: () => "",
}));

import {
  BULK_ROW_CAP,
  nextPageOf,
  resolveSelectedRows,
} from "../selectionRows";
import { datasetSelectionFilter } from "src/sections/common/Selection/exportSelection";

describe("HO D6 · select all past 500", () => {
  beforeEach(() => {
    pages.length = 0;
  });

  it("reads every page of the filtered table, minus the rows unticked", async () => {
    const rows = await resolveSelectedRows({
      datasetId: "d",
      selectAll: true,
      toggledNodes: ["r7", "r1100"],
    });
    expect(pages).toEqual([0, 1, 2]);
    expect(rows).toHaveLength(1203 - 2);
    expect(rows.some((r) => r.row_id === "r7")).toBe(false);
    expect(rows[rows.length - 1].row_id).toBe("r1202");
  });

  it("follows has_more, else total_pages, else a full page", () => {
    expect(nextPageOf({ has_more: true, next_page_index: 4 }, 3, 500)).toBe(4);
    expect(nextPageOf({ has_more: false }, 3, 500)).toBeNull();
    expect(nextPageOf({ total_pages: 3 }, 1, 500)).toBe(2);
    expect(nextPageOf({ total_pages: 3 }, 2, 500)).toBeNull();
    expect(nextPageOf(null, 0, BULK_ROW_CAP)).toBe(1);
    expect(nextPageOf(null, 0, 12)).toBeNull();
    expect(nextPageOf({ has_more: true }, 0, 0)).toBeNull();
  });

  it("Download (n) hands the export dialog exactly those rows", () => {
    expect(datasetSelectionFilter(["a", "b", "b", "c"])).toEqual({
      filters: [
        {
          column_id: "system:row_ids",
          filter_config: expect.objectContaining({
            filter_value: ["a", "b", "c"],
          }),
        },
      ],
      search: "",
      label: "3 selected rows",
    });
  });
});
