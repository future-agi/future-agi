import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import AlertsListView from "../components/AlertsListView/AlertsListView";
import { resetAlertStoreState } from "../store/useAlertStore";
import { resetAlertFilterStoreState } from "../store/useAlertFilterStore";

const mocks = vi.hoisted(() => ({ table: null }));

// jsdom has no layout, so read the Issues column the list hands the table and
// render its cell on its own.
vi.mock("src/components/data-table", async (importOriginal) => ({
  ...(await importOriginal()),
  DataTable: (props) => {
    mocks.table = props;
    return null;
  },
  DataTablePagination: () => null,
}));
vi.mock("../components/AlertsListView/Actions", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock(
  "../components/AlertsSheetView/AlertsSheetView",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

// An alert whose name is longer than its cell: the name ends in an ellipsis
// at every width instead of stopping mid-word.
const LONG_NAME =
  "Slow LLM responses on the support project eu-west-prod and its replicas";

const ZERO = /^0(px)?$/;

// getPropertyValue + toBe: a value cssstyle does not keep reads "" and fails,
// where toHaveStyle could pass vacuously.
const css = (element, property) =>
  window.getComputedStyle(element).getPropertyValue(property);

let client;

const nameColumn = async () => {
  render(
    <QueryClientProvider client={client}>
      <AlertsListView />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(mocks.table?.columns).toBeTruthy());
  return mocks.table.columns.find((col) => col.id === "name");
};

const renderNameCell = (column, original) =>
  render(
    column.cell({
      getValue: () => original.name,
      row: { original },
    }),
  );

beforeEach(() => {
  mocks.table = null;
  resetAlertStoreState();
  resetAlertFilterStoreState();
  vi.spyOn(axios, "get").mockResolvedValue({
    data: {
      result: {
        table: [{ id: "alert-a", name: LONG_NAME }],
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

describe("alerts list Issues column at 1024 and 1280", () => {
  it("fits the name box to the cell, so a long name ends in an ellipsis", async () => {
    const column = await nameColumn();
    expect(column.header).toBe("Issues");

    renderNameCell(column, { id: "alert-a", name: LONG_NAME });

    const name = screen.getByText(LONG_NAME);
    const box = name.parentElement;
    // The grid cell is a flex row that clips: the box must be allowed to be
    // narrower than the name, or the cell edge cuts it mid-word.
    expect(css(box, "display")).toBe("flex");
    expect(css(box, "min-width")).toMatch(ZERO);
    // The name itself is the block that draws the ellipsis.
    expect(css(name, "min-width")).toMatch(ZERO);
    expect(css(name, "overflow")).toBe("hidden");
    expect(css(name, "text-overflow")).toBe("ellipsis");
    expect(css(name, "white-space")).toBe("nowrap");
  });

  it("keeps the full name in the title and the row hooks on the name", async () => {
    const column = await nameColumn();

    renderNameCell(column, { id: "alert-a", name: LONG_NAME });

    const name = screen.getByText(LONG_NAME);
    expect(name).toHaveAttribute("title", LONG_NAME);
    expect(name).toHaveAttribute("data-alert-row-name", LONG_NAME);
    expect(name).toHaveAttribute("data-alert-row-id", "alert-a");
  });

  it("keeps the mute icon whole, after the shortened name", async () => {
    const column = await nameColumn();

    renderNameCell(column, { id: "alert-a", name: LONG_NAME, isMute: true });

    const name = screen.getByText(LONG_NAME);
    const box = name.parentElement;
    expect(css(box, "min-width")).toMatch(ZERO);
    const icon = box.lastElementChild;
    expect(icon).not.toBe(name);
    expect(icon).toHaveClass("svg-color");
    expect(css(icon, "flex-shrink")).toBe("0");
  });

  it("gives a nameless row no title", async () => {
    const column = await nameColumn();

    const { container } = renderNameCell(column, { id: "alert-b", name: null });

    expect(
      container.querySelector("[data-alert-row-id='alert-b']"),
    ).not.toHaveAttribute("title");
  });
});
