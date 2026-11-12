import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import AlertsListView from "../components/AlertsListView/AlertsListView";
import { resetAlertStoreState } from "../store/useAlertStore";
import { resetAlertFilterStoreState } from "../store/useAlertFilterStore";

const mocks = vi.hoisted(() => ({ table: null }));

// jsdom has no layout, so read the column the list hands the table: the
// table turns `minSize` on a flex column into the grid column's minWidth.
vi.mock("src/components/data-table", () => ({
  DataTable: (props) => {
    mocks.table = props;
    return null;
  },
  DataTablePagination: () => null,
}));
vi.mock("../components/AlertsListView/Actions", () => ({
  default: () => null,
}));
vi.mock("../components/AlertsSheetView/AlertsSheetView", () => ({
  default: () => null,
}));

// The screen pass at 1024 measured "Count of errors" at 106 px of text in a
// 50 px cell; the grid cell adds 10 px of padding on each side.
const COUNT_OF_ERRORS_TEXT = 106;
const CELL_PADDING = 20;

let client;

const renderList = async () => {
  render(
    <QueryClientProvider client={client}>
      <AlertsListView />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(mocks.table?.columns).toBeTruthy());
  return mocks.table.columns.find((col) => col.id === "metricType");
};

beforeEach(() => {
  mocks.table = null;
  resetAlertStoreState();
  resetAlertFilterStoreState();
  vi.spyOn(axios, "get").mockResolvedValue({
    data: {
      result: {
        table: [{ id: "alert-a", name: "Alpha alert" }],
        metadata: { total_rows: 1 },
        column_config: [],
      },
    },
  });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
  resetAlertStoreState();
  resetAlertFilterStoreState();
});

describe("alerts list Alert Type column at narrow widths", () => {
  it("keeps the column wide enough for 'Count of errors' and its header", async () => {
    const column = await renderList();

    expect(column.header).toBe("Alert Type");
    // minSize only reaches the grid on a flex column.
    expect(column.meta?.flex).toBeTruthy();
    expect(column.minSize).toBeGreaterThanOrEqual(
      COUNT_OF_ERRORS_TEXT + CELL_PADDING,
    );
  });

  it("fits the whole row in the 792 px grid at 1024, so Updated at is not cut", async () => {
    await renderList();

    // The grid is 792 px wide at 1024. A flex column never shrinks below its
    // minSize (MUI's 50 px when it sets none), a fixed column keeps its size,
    // and the selection checkbox takes 50 px.
    const hidden = mocks.table.columnVisibility ?? {};
    const visible = mocks.table.columns.filter(
      (col) => hidden[col.id] !== false,
    );
    const row = visible.reduce(
      (sum, col) => sum + (col.meta?.flex ? col.minSize ?? 50 : col.size),
      50,
    );
    expect(row).toBeLessThanOrEqual(792);
  });

  it("ends a longer alert type with an ellipsis instead of cutting it mid-word", async () => {
    const column = await renderList();

    render(
      column.cell({
        getValue: () => "Error rates for function calling",
        row: { original: {} },
      }),
    );

    const value = screen.getByText("Error rates for function calling");
    expect(value).toHaveClass("MuiTypography-noWrap");
    expect(getComputedStyle(value).textOverflow).toBe("ellipsis");
  });
});
