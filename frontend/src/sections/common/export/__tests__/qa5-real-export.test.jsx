import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";

const net = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: net.get },
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));
import { DatasetExportDialog } from "../ExportDialog";

const mount = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <DatasetExportDialog open datasetId="dataset-a" onClose={() => {}} />
    </QueryClientProvider>,
  );
afterEach(() => {
  cleanup();
  setRealBackendForTests(null);
  vi.restoreAllMocks();
});

it("exports current real-backend rows without requesting or exposing versions/splits", async () => {
  setRealBackendForTests(true);
  net.get.mockImplementation(async (url) => {
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
          table: [{ row_id: "r1", c1: { cell_value: "readable fixture" } }],
          metadata: { total_rows: 1, has_more: false, dataset_name: "Fixture" },
        },
      },
    };
  });
  const saved = [];
  window.URL.createObjectURL = vi.fn((blob) => {
    saved.push(blob);
    return "blob:fixture";
  });
  window.URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  mount();
  expect(
    screen.queryByRole("combobox", { name: "Version" }),
  ).not.toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByTestId("export-download")).toBeEnabled(),
  );
  fireEvent.click(screen.getByTestId("export-download"));
  expect(saved).toHaveLength(1);
  const text = await new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.readAsText(saved[0]);
  });
  expect(text).toContain("readable fixture");
  expect(
    net.get.mock.calls.every(([url]) => url.includes("/get-dataset-table/")),
  ).toBe(true);
});

it("reports a failed current-row export and offers retry instead of a silent disabled button", async () => {
  setRealBackendForTests(true);
  net.get.mockRejectedValue(new Error("offline fixture"));
  mount();
  await screen.findByText(/Couldn't read the rows/);
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  expect(screen.getByTestId("export-download")).toBeDisabled();
});
