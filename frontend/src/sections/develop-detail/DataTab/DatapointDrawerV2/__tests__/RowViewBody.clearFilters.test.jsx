import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import {
  useDevelopFilterStore,
  useDevelopSearchStore,
  useDevelopSelectedRowsStore,
} from "src/sections/develop-detail/states";
import {
  getDataGridFilters,
  getDataGridView,
  useDataGridViewStore,
} from "../../DataGrid/useDataGridView";
import { systemFilters } from "../../DataGrid/dataGridView";
import { gridNavQuery } from "../gridNavQuery";

const h = vi.hoisted(() => ({ gridApi: { current: null }, rowQuery: null }));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ gridApi: h.gridApi }),
}));
vi.mock("../RowFieldsTab", () => ({ default: () => null }));
vi.mock("../RowRunsTab", () => ({ default: () => null }));
vi.mock("../RowAnnotationsTab", () => ({ default: () => null }));
vi.mock("../RowActivityTab", () => ({ default: () => null }));
vi.mock("src/sections/common/RowLayouts/RowLayoutTab", () => ({
  default: ({ children }) => children,
}));

import RowViewBody from "../RowViewBody";

const DATASET = "dataset-a";
const MODEL = {
  input: [],
  expected: [],
  outputs: [],
  metadata: [],
  noRole: [],
};
const SORT = [{ colId: "question", sort: "desc" }];
const SYSTEM_COLS = { source: false, updated: true, splits: true };
const OTHER = { failedOnly: true, splitIds: ["other-split"], density: "tall" };
let client;

// Model the outside-set result from the same navigation encoder the drawer
// uses. Neither the grid nor the row read may keep a hidden narrowing flag.
function Drawer() {
  const filters = useDevelopFilterStore((s) => s.filters);
  const search = useDevelopSearchStore((s) => s.search);
  const view = useDataGridViewStore((s) => s.views[DATASET]);
  h.rowQuery = gridNavQuery({
    filters,
    search,
    systemFilters: systemFilters(view),
    columnState: view.sort,
  });
  return (
    <RowViewBody
      datasetId={DATASET}
      rowId="row-a"
      row={{ row_id: "row-a" }}
      model={MODEL}
      nav={{ outside: h.rowQuery.narrowed }}
      tab="row"
      setTab={() => {}}
      view={{}}
      edit={{}}
      actions={{}}
    />
  );
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  useDevelopFilterStore.getState().setFilters([
    {
      columnId: "question",
      filterConfig: {
        filterType: "text",
        filterOp: "contains",
        filterValue: "draft",
      },
    },
  ]);
  useDevelopSearchStore.setState({ search: "hidden" });
  useDevelopSelectedRowsStore.setState({
    selectAll: true,
    toggledNodes: ["row-b"],
  });
  useDataGridViewStore.setState({ views: { other: OTHER } });
  useDataGridViewStore.getState().patch(DATASET, {
    failedOnly: true,
    failedEvalIds: ["quality"],
    splitIds: ["split-a"],
    pendingSplitNames: "hard",
    cellErrorOnly: true,
    rowIds: ["row-b"],
    rowIdsLabel: "Selected rows",
    sourceKind: "trace",
    sourceProject: "project-a",
    sourceProjectName: "Project A",
    invalidOnly: true,
    statuses: ["approved"],
    tags: ["review"],
    collab: { key: "assigned_to", value: "me", label: "Mine" },
    expression: "question != null",
    density: "comfortable",
    systemCols: SYSTEM_COLS,
    sort: SORT,
  });
  h.gridApi.current = {
    deselectAll: vi.fn(),
    onFilterChanged: vi.fn(() => ({
      filters: getDataGridFilters(DATASET),
      search: useDevelopSearchStore.getState().search,
    })),
  };
});
afterEach(() => {
  cleanup();
  client.clear();
  useDevelopFilterStore.getState().resetFilters();
  useDevelopSearchStore.setState({ search: "" });
  useDevelopSelectedRowsStore.setState({ selectAll: false, toggledNodes: [] });
  useDataGridViewStore.setState({ views: {} });
});

it("clears every narrowing filter without changing presentation or another dataset", () => {
  render(
    <QueryClientProvider client={client}>
      <Drawer />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(getDataGridFilters(DATASET)).toEqual([]);
  expect(useDevelopSearchStore.getState().search).toBe("");
  expect(h.rowQuery.filters).toEqual([]);
  expect(h.rowQuery.search).toBeUndefined();
  expect(h.rowQuery.narrowed).toBe(false);
  expect(screen.queryByTestId("row-banner-outside")).toBeNull();
  expect(h.gridApi.current.onFilterChanged).toHaveBeenCalledTimes(1);
  expect(h.gridApi.current.onFilterChanged.mock.results[0].value).toEqual({
    filters: [],
    search: "",
  });
  const view = getDataGridView(DATASET);
  expect(view.pendingSplitNames).toBeNull();
  expect(view.density).toBe("comfortable");
  expect(view.systemCols).toEqual(SYSTEM_COLS);
  expect(view.sort).toEqual(SORT);
  expect(getDataGridView("other")).toEqual(OTHER);
  expect(h.gridApi.current.deselectAll).toHaveBeenCalledTimes(1);
  const { selectAll, toggledNodes } = useDevelopSelectedRowsStore.getState();
  expect({ selectAll, toggledNodes }).toEqual({
    selectAll: false,
    toggledNodes: [],
  });
});
