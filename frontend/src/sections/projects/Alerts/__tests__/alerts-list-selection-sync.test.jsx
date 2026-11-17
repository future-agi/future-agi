import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import AlertsListView from "../components/AlertsListView/AlertsListView";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import { resetAlertFilterStoreState } from "../store/useAlertFilterStore";

const mocks = vi.hoisted(() => ({ table: null }));

// Record what the list hands the table on every render: `rowSelection` is
// which checkboxes are ticked, `onRowSelectionChange` is a user tick.
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

const ALERTS = [
  { id: "alert-a", name: "Alpha alert" },
  { id: "alert-b", name: "Beta alert" },
  { id: "alert-c", name: "Gamma alert" },
];

let client;

const renderList = async () => {
  render(
    <QueryClientProvider client={client}>
      <AlertsListView />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(mocks.table?.data).toHaveLength(3));
};

const tick = (selection) =>
  act(() => mocks.table.onRowSelectionChange(
    selection,
    mocks.table.data.filter((row) => selection[row.id]),
  ));

const selectedIds = () =>
  useAlertStore.getState().selectedRows.map((row) => row.id);

beforeEach(() => {
  mocks.table = null;
  resetAlertStoreState();
  resetAlertFilterStoreState();
  vi.spyOn(axios, "get").mockResolvedValue({
    data: {
      result: {
        table: ALERTS,
        metadata: { total_rows: ALERTS.length },
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

describe("alerts list checkboxes follow the store selection", () => {
  it("unticks an alert that was dropped from the selection", async () => {
    await renderList();
    tick({ "alert-a": true, "alert-b": true, "alert-c": true });
    expect(selectedIds()).toEqual(["alert-a", "alert-b", "alert-c"]);

    act(() => {
      const [alpha, , gamma] = useAlertStore.getState().selectedRows;
      useAlertStore.setState({ selectedRows: [alpha, gamma] });
    });

    expect(mocks.table.rowSelection).toEqual({ "alert-a": true, "alert-c": true });
  });

  it("does not add an alert removed in the confirm dialog back on the next tick", async () => {
    await renderList();
    tick({ "alert-a": true, "alert-b": true });

    act(() => {
      useAlertStore.getState().handleOpenActionModal("delete");
      useAlertStore.getState().handleRemoveRow("alert-b");
    });

    expect(selectedIds()).toEqual(["alert-a"]);
    expect(mocks.table.rowSelection).toEqual({ "alert-a": true });

    // The table reports a new tick on top of what it currently shows.
    tick({ ...mocks.table.rowSelection, "alert-c": true });

    expect(selectedIds()).toEqual(["alert-a", "alert-c"]);
  });

  it("clears every tick when the selection is cancelled", async () => {
    await renderList();
    tick({ "alert-a": true, "alert-c": true });

    act(() => useAlertStore.getState().handleCancelSelection());

    expect(mocks.table.rowSelection).toEqual({});
  });
});
