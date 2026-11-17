// HO D6 integration (merged in M11): the dataset Download dialog's Rows select
// names a row selection by its own label ("3 selected rows"), not "The current
// filter (3)". A grid filter without a label keeps "The current filter (n)".
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    develop: {
      getDatasetDetail: (id) => `/model-hub/develops/${id}/get-dataset-table/`,
      versions: {
        list: (id) => `/model-hub/datasets/${id}/versions/`,
        table: (id, v) => `/model-hub/datasets/${id}/versions/${v}/table/`,
      },
      splits: { list: (id) => `/model-hub/datasets/${id}/splits/` },
      datasetRuns: { results: (id) => `/model-hub/datasets/${id}/results/` },
    },
  },
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));

const { DatasetExportDialog } = await import("../ExportDialog");
const { datasetSelectionFilter } = await import(
  "src/sections/common/Selection/exportSelection"
);

const COLUMNS = [
  {
    id: "ci",
    name: "customer_message",
    data_type: "text",
    origin_type: "OTHERS",
  },
];
const ROWS = ["r1", "r2", "r3", "r4"].map((id, i) => ({
  row_id: id,
  order: i,
  ci: { cell_value: `message ${i}` },
  row_meta: { split_ids: [], metadata: {} },
}));
mocks.get.mockImplementation(async (url, { params = {} } = {}) => {
  if (url.endsWith("/versions/"))
    return {
      data: {
        result: {
          versions: [{ id: "V1", number: 1, is_latest: true, row_count: 4 }],
        },
      },
    };
  if (url.endsWith("/splits/")) return { data: { result: { splits: [] } } };
  if (params.column_config_only)
    return { data: { result: { column_config: COLUMNS } } };
  const filters = JSON.parse(params.filters ?? "[]");
  const ids = filters.flatMap((f) => f?.filter_config?.filter_value ?? []);
  const table = ids.length ? ROWS.filter((r) => ids.includes(r.row_id)) : ROWS;
  return {
    data: {
      result: {
        column_config: COLUMNS,
        table,
        metadata: {
          total_rows: table.length,
          dataset_name: "Golden",
          has_more: false,
        },
      },
    },
  };
});

const renderDialog = (currentFilter) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <DatasetExportDialog
        open
        onClose={() => {}}
        datasetId="DS"
        currentFilter={currentFilter}
      />
    </QueryClientProvider>,
  );
const rowsText = () =>
  within(screen.getByTestId("dataset-export-dialog")).getByTestId("export-rows")
    .previousSibling.textContent;

describe("Download dialog Rows line (HO D6)", () => {
  it("a row selection names itself", async () => {
    const selection = datasetSelectionFilter(["r1", "r2", "r3"]);
    expect(selection.label).toBe("3 selected rows");
    renderDialog(selection);
    await within(screen.getByTestId("dataset-export-dialog")).findByTestId(
      "export-size",
    );
    expect(rowsText()).toBe(selection.label);
    expect(rowsText()).not.toMatch(/The current filter/);
  });

  it("a grid filter without a label keeps 'The current filter'", async () => {
    renderDialog({ filters: [], search: "message" });
    await within(screen.getByTestId("dataset-export-dialog")).findByTestId(
      "export-size",
    );
    expect(rowsText()).toMatch(/^The current filter/);
  });
});
