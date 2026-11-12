import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const net = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: net.get },
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));
import { DatasetExportDialog } from "../ExportDialog";

const mount = (props = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <DatasetExportDialog
        open
        datasetId="dataset-export"
        onClose={() => {}}
        {...props}
      />
    </QueryClientProvider>,
  );
beforeEach(() => {
  setRealBackendForTests(true);
  setLaunchOverrideForTest({ launch: true, madeReal: ["dataset.export"] });
  net.get.mockReset();
  net.get.mockImplementation(async (url, { params }) => {
    expect(url).toContain("/get-dataset-table/");
    return {
      data: {
        result: {
          column_config: [
            {
              id: "c1",
              name: "Question",
              data_type: "text",
              origin_type: "OTHERS",
            },
          ],
          table:
            params.current_page_index === 0
              ? [
                  { row_id: "r1", c1: { cell_value: "one" } },
                  { row_id: "r2", c1: { cell_value: "two" } },
                ]
              : [{ row_id: "r3", c1: { cell_value: "three" } }],
          metadata: {
            total_rows: 3,
            has_more: params.current_page_index === 0,
            dataset_name: "Fixture",
          },
        },
      },
    };
  });
});
afterEach(() => {
  cleanup();
  setRealBackendForTests(null);
  setLaunchOverrideForTest(null);
  vi.restoreAllMocks();
});
it("keeps ordinary CSV and real rows available without the advanced export capability", async () => {
  // MAIN-RULING-EXPORT-CONTRACT: the claim gates advanced formats, not CSV.
  // Only current-row reads and a CSV download may occur with the gate closed.
  setLaunchOverrideForTest({ launch: true, madeReal: [] });
  const originalCreate = window.URL.createObjectURL;
  const originalRevoke = window.URL.revokeObjectURL;
  const saved = [];
  window.URL.createObjectURL = vi.fn((blob) => {
    saved.push(blob);
    return "blob:csv-export";
  });
  window.URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  try {
    mount();
    expect(screen.getByTestId("dataset-export-dialog")).toBeInTheDocument();
    expect(screen.getAllByRole("radio").map((input) => input.value)).toEqual([
      "csv",
    ]);
    await waitFor(() =>
      expect(screen.getByTestId("export-download")).toBeEnabled(),
    );
    expect(screen.getByTestId("export-file-name").textContent).toMatch(/\.csv$/);
    fireEvent.click(screen.getByTestId("export-download"));
    expect(saved).toHaveLength(1);
    expect(saved[0].type).toBe("text/csv");
    const text = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.readAsText(saved[0]);
    });
    for (const value of ["one", "two", "three"]) expect(text).toContain(value);
    // No advanced-format endpoint or format parameter can slip through.
    expect(net.get).toHaveBeenCalledTimes(2);
    for (const [url, { params }] of net.get.mock.calls) {
      expect(url).toContain("/get-dataset-table/");
      expect(params.filters).toBe("[]");
      expect(params).not.toHaveProperty("format");
    }
  } finally {
    window.URL.createObjectURL = originalCreate;
    window.URL.revokeObjectURL = originalRevoke;
  }
});
it("offers CSV and the gated advanced formats and always reads all current rows, not a passed filter or version", async () => {
  mount({
    currentFilter: {
      filters: [{ column_id: "foreign" }],
      search: "selected",
      label: "Selected rows",
    },
    versionNumber: 9,
  });
  await waitFor(() =>
    expect(screen.getByTestId("export-download")).toBeEnabled(),
  );
  // MAIN-RULING-EXPORT-CONTRACT: the open format gate exposes all five
  // advanced writers; real-backend mode does not impose a CSV/JSON-only cap.
  expect(screen.getAllByRole("radio").map((input) => input.value)).toEqual([
    "csv",
    "json",
    "jsonl",
    "parquet",
    "openai_ft",
    "openai_evals",
  ]);
  expect(
    screen.queryByRole("combobox", { name: "Version" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("combobox", { name: "Rows" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("All current rows")).toBeInTheDocument();
  expect(net.get).toHaveBeenCalledTimes(2);
  for (const [, { params }] of net.get.mock.calls) {
    expect(params.filters).toBe("[]");
    expect(params.search).toBeUndefined();
  }
});
it("re-authorizes on reopening instead of exporting cached rows after a refusal", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const content = (open) => (
    <QueryClientProvider client={client}>
      <DatasetExportDialog
        open={open}
        datasetId="dataset-export"
        onClose={() => {}}
      />
    </QueryClientProvider>
  );
  const view = render(content(true));
  await waitFor(() =>
    expect(screen.getByTestId("export-download")).toBeEnabled(),
  );
  view.rerender(content(false));
  net.get.mockRejectedValue(new Error("Dataset no longer accessible"));
  view.rerender(content(true));
  await screen.findByText(/Couldn't read the rows/);
  expect(screen.getByTestId("export-download")).toBeDisabled();
});
it("exports every paginated row as JSON with exact values", async () => {
  const saved = [];
  window.URL.createObjectURL = vi.fn((blob) => {
    saved.push(blob);
    return "blob:export";
  });
  window.URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  mount();
  await waitFor(() =>
    expect(screen.getByTestId("export-download")).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("radio", { name: /^JSON\s/ }));
  fireEvent.click(screen.getByTestId("export-download"));
  const text = await new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsText(saved[0]);
  });
  const body = JSON.parse(text);
  expect(body.meta.row_count).toBe(3);
  expect(body.rows.map((row) => row.row_id)).toEqual(["r1", "r2", "r3"]);
  expect(body.rows.map((row) => row.fields.Question)).toEqual([
    "one",
    "two",
    "three",
  ]);
});
