import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import {
  setLaunchOverrideForTest,
  isLaunchBuild,
} from "src/launch/launchSwitch";
import {
  isRealBackend,
  setRealBackendForTests,
} from "src/sections/develop/realBackendCaps";
import { DatasetExportDialog } from "../ExportDialog";

const net = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: net.get },
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));
const originalCreate = window.URL.createObjectURL;
const originalRevoke = window.URL.revokeObjectURL;
let saved;
beforeEach(() => {
  setRealBackendForTests(null);
  setLaunchOverrideForTest({ launch: true, madeReal: [] });
  net.get.mockReset();
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
  saved = [];
  window.URL.createObjectURL = vi.fn((blob) => {
    saved.push(blob);
    return "blob:fixture";
  });
  window.URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  setRealBackendForTests(null);
  vi.restoreAllMocks();
  window.URL.createObjectURL = originalCreate;
  window.URL.revokeObjectURL = originalRevoke;
});
const mount = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const close = vi.fn();
  const element = () => (
    <QueryClientProvider client={client}>
      <DatasetExportDialog open datasetId="dataset-a" onClose={close} />
    </QueryClientProvider>
  );
  const view = render(element());
  return { close, rerender: () => view.rerender(element()) };
};
it("hides only non-CSV formats while ordinary CSV still downloads real-backend rows", async () => {
  const { close } = mount();
  expect(isLaunchBuild()).toBe(true);
  expect(isRealBackend()).toBe(true);
  expect(screen.getAllByRole("radio").map((input) => input.value)).toEqual([
    "csv",
  ]);
  await waitFor(() =>
    expect(screen.getByTestId("export-download")).toBeEnabled(),
  );
  fireEvent.click(screen.getByTestId("export-download"));
  expect(close).toHaveBeenCalledOnce();
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
it("cannot retain an advanced selection when its independent format gate closes", async () => {
  setLaunchOverrideForTest({ launch: true, madeReal: ["dataset.export"] });
  const { rerender } = mount();
  expect(screen.getAllByRole("radio").map((input) => input.value)).toEqual([
    "csv",
    "json",
    "jsonl",
    "parquet",
    "openai_ft",
    "openai_evals",
  ]);
  await waitFor(() =>
    expect(screen.getByTestId("export-download")).toBeEnabled(),
  );
  fireEvent.click(
    screen.getAllByRole("radio").find((input) => input.value === "json"),
  );
  expect(screen.getByTestId("export-file-name").textContent).toMatch(/\.json$/);
  setLaunchOverrideForTest({ launch: true, madeReal: [] });
  rerender();
  expect(screen.getAllByRole("radio").map((input) => input.value)).toEqual([
    "csv",
  ]);
  expect(screen.getByTestId("export-file-name").textContent).toMatch(/\.csv$/);
  expect(isRealBackend()).toBe(true);
});
it("preserves the current-row export failure and retry control", async () => {
  net.get.mockRejectedValue(new Error("offline fixture"));
  mount();
  await screen.findByText(/Couldn't read the rows/);
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  expect(screen.getByTestId("export-download")).toBeDisabled();
  expect(saved).toEqual([]);
});
