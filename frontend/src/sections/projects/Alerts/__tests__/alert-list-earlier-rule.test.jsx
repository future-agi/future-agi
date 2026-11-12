import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { ROW_HEIGHT } from "src/theme/sizes";
import AlertsListView from "../components/AlertsListView/AlertsListView";
import { resetAlertStoreState } from "../store/useAlertStore";
import { resetAlertFilterStoreState } from "../store/useAlertFilterStore";

const mocks = vi.hoisted(() => ({ table: null }));

// Read the rows and columns the list hands the table, then render one cell.
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

const ROW_LINE = "Uses the rule it was saved with";

// List items as the monitor list returns them: snake_case, with the rule the
// alert is judged by and the kind of threshold it was saved with.
const listRow = (id, overrides = {}) => ({
  id,
  name: `Alert ${id}`,
  metric_type: "Count of errors",
  threshold_type: "percentage_change",
  ...overrides,
});

const EARLIER_PERCENTAGE = listRow("earlier-percentage", {
  threshold_rule: "earlier",
});
const EARLIER_STATIC = listRow("earlier-static", {
  threshold_rule: "earlier",
  threshold_type: "static",
});
const CURRENT_PERCENTAGE = listRow("current-percentage", {
  threshold_rule: "current",
});
// An older server sends no threshold_rule: the alert is on the current rule.
const NO_RULE_PERCENTAGE = listRow("no-rule-percentage");

let client;

const renderList = async (table) => {
  vi.spyOn(axios, "get").mockResolvedValue({
    data: {
      result: {
        table,
        metadata: { total_rows: table.length },
        column_config: [],
      },
    },
  });
  render(
    <QueryClientProvider client={client}>
      <AlertsListView />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(mocks.table?.data).toHaveLength(table.length));
};

// The name cell for one row, from the row the list itself hands the table.
const renderNameCell = (id) => {
  const item = mocks.table.data.find((row) => row.id === id);
  const column = mocks.table.columns.find((col) => col.id === "name");
  return render(
    column.cell({ getValue: () => item.name, row: { original: item } }),
  );
};

beforeEach(() => {
  mocks.table = null;
  resetAlertStoreState();
  resetAlertFilterStoreState();
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

describe("alerts list row for an alert on its earlier rule", () => {
  it("adds one quiet line under the name of an earlier percentage alert", async () => {
    await renderList([EARLIER_PERCENTAGE, CURRENT_PERCENTAGE]);

    renderNameCell(EARLIER_PERCENTAGE.id);

    expect(screen.getByText(EARLIER_PERCENTAGE.name)).toBeInTheDocument();
    expect(screen.getByText(ROW_LINE)).toBeInTheDocument();
  });

  it.each([
    ["a static alert saved before the update", EARLIER_STATIC],
    ["a percentage alert on the current rule", CURRENT_PERCENTAGE],
    ["a percentage alert from a server without the rule", NO_RULE_PERCENTAGE],
  ])("adds no line for %s", async (_kind, row) => {
    await renderList([EARLIER_PERCENTAGE, row]);

    renderNameCell(row.id);

    expect(screen.getByText(row.name)).toBeInTheDocument();
    expect(screen.queryByText(ROW_LINE)).not.toBeInTheDocument();
  });

  it("gives the page two-line rows when one row has the line", async () => {
    await renderList([EARLIER_PERCENTAGE, CURRENT_PERCENTAGE]);

    expect(mocks.table.rowHeight).toBe(ROW_HEIGHT.twoLine);
  });

  it("keeps one-line rows on a page without the line", async () => {
    await renderList([EARLIER_STATIC, CURRENT_PERCENTAGE, NO_RULE_PERCENTAGE]);

    expect(mocks.table.rowHeight).toBe(ROW_HEIGHT.default);
  });
});
