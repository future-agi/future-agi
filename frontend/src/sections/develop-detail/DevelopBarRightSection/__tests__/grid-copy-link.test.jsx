import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import { act, cleanup, renderWithRouter, waitFor } from "src/utils/test-utils";
import { useCommandStore } from "src/sections/common/CommandPalette/commandRegistry";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import { useDevelopFilterStore, useDevelopSearchStore } from "../../states";
import {
  getDataGridFilters,
  getDataGridView,
  useDataGridViewStore,
} from "../../DataTab/DataGrid/useDataGridView";
import { systemFilters } from "../../DataTab/DataGrid/dataGridView";
import { gridNavQuery } from "../../DataTab/DatapointDrawerV2/gridNavQuery";
import {
  GRID_VIEW_PARAM,
  readGridViewLink,
} from "../../DataTab/DataGrid/gridViewLink";

const h = vi.hoisted(() => ({
  copy: vi.fn(),
  patchLayout: vi.fn(),
  layout: {},
  gridApi: { current: null },
  Null: () => null,
  noop: () => {},
}));
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ gridApi: h.gridApi }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: (id) => ({
    queryKey: ["grid-link", id],
    queryFn: () => Promise.resolve({}),
    enabled: false,
    initialData: { data: { result: { table: [{ row_id: "row-a" }] } } },
  }),
}));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  useLayoutPrefs: () => [h.layout, h.patchLayout],
}));
vi.mock("../../DataTab/DataGrid/useDataGridLayout", () => ({
  default: () => ({ prefs: h.layout.columns, openMenu: h.noop }),
}));
vi.mock("src/sections/common/ux/CopyButton", () => ({ copyText: h.copy }));
vi.mock("src/sections/common/ux/toast", () => ({ notify: h.noop }));
vi.mock("src/sections/common/ux/useShortcuts", () => ({ default: h.noop }));
vi.mock("src/utils/Mixpanel/mixpanel", () => ({ trackEvent: h.noop }));
vi.mock("src/components/iconify/iconify", () => ({ default: h.Null }));
vi.mock("src/components/svg-color/svg-color", () => ({ default: h.Null }));
vi.mock("../../DataTab/Versions/useVersionUrl", () => ({
  default: () => ({ toggleHistory: h.noop }),
}));
vi.mock("../../DataTab/DatapointDrawerV2/useRowViewUrl", () => ({
  default: () => ({ openRow: h.noop }),
}));
vi.mock("../../DataTab/Splits/SplitFilterButton", () => ({ default: h.Null }));
vi.mock("../../DataTab/DataGrid/ToolbarControls", () => ({
  FailedOnlyChip: h.Null,
}));
vi.mock("../../DataTab/DataGrid/GoToRowPopover", () => ({ default: h.Null }));
vi.mock("../../DataTab/DataGrid/CellPeekPopover", () => ({ default: h.Null }));
vi.mock("src/sections/common/filters/ExpressionFilterButton", () => ({
  default: h.Null,
}));

import DevelopBarLeftSection from "../DevelopBarLeftSection";

const DATASET = "dataset-a";
const FILTERS = [
  {
    columnId: "question",
    filterConfig: {
      filterType: "text",
      filterOp: "contains",
      filterValue: "keep",
    },
  },
];
const SORT = [{ colId: "question", sort: "desc" }];
const EXPR = 'len(question) > 12 and answer != "skip & wait"';
const COLUMN_FILTER = {
  column_id: "question",
  filter_config: {
    filter_type: "text",
    filter_op: "contains",
    filter_value: "keep",
  },
};
const EXPRESSION_FILTER = {
  column_id: "system:expression",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: EXPR,
  },
};
const rowScope = () => {
  const view = getDataGridView(DATASET);
  return gridNavQuery({
    filters: useDevelopFilterStore.getState().filters,
    search: useDevelopSearchStore.getState().search,
    systemFilters: systemFilters(view),
    columnState: view.sort,
  });
};
let client;
const mount = (route) =>
  renderWithRouter(
    <QueryClientProvider client={client}>
      <Routes>
        <Route path="/develop/:dataset" element={<DevelopBarLeftSection />} />
      </Routes>
    </QueryClientProvider>,
    { route },
  );

beforeEach(() => {
  setLaunchOverrideForTest({ launch: false });
  setRealBackendForTests(false);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  h.copy.mockReset().mockResolvedValue(true);
  h.patchLayout.mockReset();
  h.layout = {
    columns: { visible: { answer: false }, order: ["question", "answer"] },
  };
  h.gridApi.current = {
    deselectAll: vi.fn(),
    onFilterChanged: vi.fn(),
    applyColumnState: vi.fn(),
    getColumns: () => [{ getColId: () => "question" }],
  };
  useDevelopFilterStore.getState().resetFilters();
  useDevelopSearchStore.setState({ search: "" });
  useDataGridViewStore.setState({ views: {} });
});
afterEach(() => {
  cleanup();
  client.clear();
  useCommandStore.setState({ sources: {} });
  useDevelopFilterStore.getState().resetFilters();
  useDevelopSearchStore.setState({ search: "" });
  useDataGridViewStore.setState({ views: {} });
  setLaunchOverrideForTest(null);
  setRealBackendForTests(null);
});

it("copies current column filters and sorting through the registered command", async () => {
  const mounted = mount(`/develop/${DATASET}`);
  const command = useCommandStore
    .getState()
    .sources["dataset-grid"].find(({ id }) => id === "grid.copyLink");
  // Capture the registered command before the user changes the view. It must
  // call the latest closure, not the state from its initial registration.
  act(() => {
    useDevelopFilterStore.getState().setFilters(FILTERS);
    useDevelopSearchStore.getState().setSearch("needle");
    useDataGridViewStore.getState().patch(DATASET, {
      sort: SORT,
      density: "tall",
    });
  });
  await act(async () => {
    await command.run();
  });
  expect(h.copy).toHaveBeenCalledTimes(1);
  const url = new URL(h.copy.mock.calls[0][0]);
  expect(readGridViewLink(url.searchParams.get(GRID_VIEW_PARAM))).toEqual({
    q: "needle",
    density: "tall",
    filters: FILTERS,
    sort: SORT,
    failed: false,
    fevals: [],
    layout: h.layout,
  });
  mounted.unmount();
  // Opening the copied link with fresh stores runs the real hydration hook.
  useDevelopFilterStore.getState().resetFilters();
  useDevelopSearchStore.setState({ search: "" });
  useDataGridViewStore.setState({ views: {} });
  useDataGridViewStore.getState().patch(DATASET, { readyAt: 1 });
  h.patchLayout.mockClear();
  mount(`${url.pathname}${url.search}`);
  await waitFor(() => {
    expect(useDevelopFilterStore.getState().filters).toEqual(FILTERS);
  });
  expect(useDevelopSearchStore.getState().search).toBe("needle");
  expect(getDataGridView(DATASET).sort).toEqual(SORT);
  expect(getDataGridView(DATASET).density).toBe("tall");
  expect(h.gridApi.current.applyColumnState.mock.calls).toEqual([
    [
      {
        state: [{ colId: "question", sort: "desc", sortIndex: 0 }],
        defaultState: { sort: null, sortIndex: null },
      },
    ],
  ]);
  expect(h.patchLayout.mock.calls[0][0]({ columns: {} })).toEqual(h.layout);
});

it.each([
  {
    name: "restores the copied expression and exact row scope in the prototype",
    real: false,
    expression: EXPR,
    filters: [COLUMN_FILTER, EXPRESSION_FILTER],
  },
  {
    name: "suppresses a copied expression on the real backend without losing column scope",
    real: true,
    expression: null,
    filters: [COLUMN_FILTER],
  },
])("$name", async ({ real, expression, filters }) => {
  const mounted = mount(`/develop/${DATASET}?expr=old`);
  const command = useCommandStore
    .getState()
    .sources["dataset-grid"].find(({ id }) => id === "grid.copyLink");
  act(() => {
    useDevelopFilterStore.getState().setFilters(FILTERS);
    useDevelopSearchStore.getState().setSearch("needle");
    useDataGridViewStore.getState().patch(DATASET, {
      expression: EXPR,
      sort: SORT,
      density: "tall",
    });
  });
  const originalScope = rowScope();
  expect(getDataGridFilters(DATASET)).toEqual([
    COLUMN_FILTER,
    EXPRESSION_FILTER,
  ]);
  await act(async () => {
    await command.run();
  });
  expect(h.copy).toHaveBeenCalledTimes(1);
  const url = new URL(h.copy.mock.calls[0][0]);
  expect(url.searchParams.has("expr")).toBe(false);
  expect(readGridViewLink(url.searchParams.get(GRID_VIEW_PARAM))).toEqual({
    q: "needle",
    density: "tall",
    filters: FILTERS,
    sort: SORT,
    failed: false,
    fevals: [],
    expr: EXPR,
    layout: h.layout,
  });
  mounted.unmount();
  // Reopen the actual copied URL in a fresh view. The real-backend control
  // goes through applyView -> setExpression, not a mocked capability check.
  useDevelopFilterStore.getState().resetFilters();
  useDevelopSearchStore.setState({ search: "" });
  useDataGridViewStore.setState({ views: {} });
  setRealBackendForTests(real);
  useDataGridViewStore.getState().patch(DATASET, { readyAt: 1 });
  mount(`${url.pathname}${url.search}`);
  await waitFor(() => {
    expect(useDevelopFilterStore.getState().filters).toEqual(FILTERS);
  });
  expect(getDataGridView(DATASET).expression).toBe(expression);
  expect(getDataGridFilters(DATASET)).toEqual(filters);
  const expectedSort = [{ column_id: "question", type: "descending" }];
  expect(rowScope()).toEqual({
    filters,
    sort: expectedSort,
    tableSort: expectedSort,
    useGridNavigation: false,
    search: { key: "needle", type: ["text", "image", "audio"] },
    total: undefined,
    narrowed: true,
    key: JSON.stringify([filters, expectedSort, "needle"]),
  });
  if (!real) expect(rowScope()).toEqual(originalScope);
});
