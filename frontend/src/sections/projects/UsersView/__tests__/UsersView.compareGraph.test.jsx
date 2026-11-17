import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderWithRouter, screen, waitFor } from "src/utils/test-utils";

// Compare on a project's Users tab draws a second graph with its own filters
// and date window, as on the Trace tab. The real UsersView runs; the graph,
// grid, toolbar, transport and page chrome are replaced.
const storedValues = new Map();
vi.stubGlobal("localStorage", {
  clear: () => storedValues.clear(),
  getItem: (key) => storedValues.get(key) ?? null,
  removeItem: (key) => storedValues.delete(key),
  setItem: (key, value) => storedValues.set(key, String(value)),
});

const h = vi.hoisted(() => {
  const iso = (range) => range.map((value) => new Date(value).toISOString());
  // Save view and Share with project hand the open view a refreshed
  // baseline: its config, here with a new row height to show it landed.
  const refreshed = (config) => ({
    ...config,
    display: { ...config.display, cellHeight: "Large" },
  });
  return {
    saveView: vi.fn(({ config }, { onSuccess }) =>
      onSuccess({ data: { result: { config: refreshed(config) } } }),
    ),
    share: vi.fn(({ config, onSaved }) =>
      onSaved({ id: "saved", config: refreshed(config) }, { created: false }),
    ),
    // The URL the page opens with, by useUrlState key.
    url: {},
    // Latest props of each graph, by its label.
    graphs: {},
    toolbar: null,
    // Opens a saved view (or null, the default) through the header context.
    openView: null,
    header: {
      setHeaderConfig: () => {},
      setActiveViewConfig: () => {},
      registerGetViewConfig: () => {},
    },
    iso,
    // The created_at window useLLMTracingFilters puts in validatedFilters.
    windowItem: (range) => ({
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: iso(range),
      },
    }),
  };
});

vi.mock("src/utils/axios", () => ({
  readQuery: vi.fn(),
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    project: {
      getUsersList: () => "/tracer/users/",
      getUsersAggregateGraphData: () => "/tracer/users/graph/",
    },
  },
}));
vi.mock("../../LLMTracing/GraphSection/PrimaryGraph", () => ({
  default: (props) => {
    h.graphs[props.graphLabel] = props;
    return <div data-testid="users-graph">{props.graphLabel}</div>;
  },
}));
vi.mock("../UsersGrid", () => ({
  default: () => <div data-testid="users-grid" />,
}));
vi.mock("../UsersEmptyScreen", () => ({ default: () => null }));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "test-workspace" }),
}));
vi.mock("src/sections/project/context/ObserveHeaderContext", async () => {
  const { useState } = await import("react");
  return {
    // Holds the open saved view (none at first); h.openView switches it, as
    // picking a view tab does.
    useObserveHeader: () => {
      const [activeViewConfig, setActiveViewConfig] = useState(null);
      h.openView = setActiveViewConfig;
      return { ...h.header, activeViewConfig, setActiveViewConfig };
    },
  };
});
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return {
    useUrlState: (key, initial) =>
      useState(key in h.url ? h.url[key] : initial),
  };
});
vi.mock("src/api/project/saved-views", () => ({
  useUpdateSavedView: () => ({ mutate: h.saveView }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useSetProjectDefaultView: () => h.share,
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
// Keyed on the hook's URL keys, like the real one: each date key holds its
// own window and carries it in validatedFilters as a created_at item.
vi.mock("../../LLMTracing/useLLMTracingFilters", async () => {
  const { useMemo, useState } = await import("react");
  return {
    useLLMTracingFilters: (defaultFilter, defaultDate, filterKey, dateKey) => {
      const [filters, setFilters] = useState(
        filterKey in h.url ? h.url[filterKey] : defaultFilter,
      );
      const [dateFilter, setDateFilter] = useState(
        dateKey in h.url ? h.url[dateKey] : defaultDate,
      );
      const validatedFilters = useMemo(
        () => [h.windowItem(dateFilter.dateFilter)],
        [dateFilter],
      );
      return {
        filters,
        setFilters,
        validatedFilters,
        dateFilter,
        setDateFilter,
      };
    },
  };
});
vi.mock("../../LLMTracing/useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: [],
    inventoryControlProps: {},
  }),
}));
vi.mock("../../LLMTracing/ObserveToolbar", () => ({
  default: (props) => {
    h.toolbar = props;
    return null;
  },
}));
vi.mock("../../LLMTracing/FilterChips", () => ({ default: () => null }));
vi.mock("../../LLMTracing/CustomColumnDialog", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);

import UsersView from "../UsersView";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const PROJECT_USERS = `/dashboard/observe/${PROJECT}/users`;
const COMPARE_WINDOW = {
  dateFilter: ["2026-09-01T00:00:00.000Z", "2026-09-08T00:00:00.000Z"],
  dateOption: "Custom",
};
const userIs = (name) => ({
  column_id: "user_id",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: name,
  },
});
const PRIMARY_ROW = userIs("Alice");
const COMPARE_ROW = userIs("Bob");

const renderAt = (route) =>
  renderWithRouter(
    <HelmetProvider>
      <Routes>
        <Route
          path="/dashboard/observe/:observeId/users"
          element={<UsersView />}
        />
        <Route path="/dashboard/users" element={<UsersView />} />
      </Routes>
    </HelmetProvider>,
    { route },
  );

const graphLabels = () =>
  screen.getAllByTestId("users-graph").map((node) => node.textContent);
const primaryGraph = () => h.graphs["User Metrics"];
const compareGraph = () => h.graphs["Compare Graph"];

// The window a graph draws: PrimaryGraph keeps an explicit created_at window
// from its filters over its dateFilter (combineGraphFilters).
const drawnWindow = ({ filters = [], dateFilter }) => {
  const item = filters.find((f) => f?.column_id === "created_at");
  return item ? item.filter_config.filter_value : h.iso(dateFilter.dateFilter);
};

// Opens a saved view as its tab does: the URL names it, and the header
// context carries its config.
const openSavedView = (id, config) => {
  window.history.replaceState({}, "", `${PROJECT_USERS}?tab=view-${id}`);
  act(() => h.openView(config));
};

// Opens a saved view and sets a filter from the compare graph's own pill.
const setCompareFilterOnSavedView = async (id) => {
  openSavedView(id, { extra_filters: [], display: {} });
  const anchor = document.createElement("button");
  act(() => compareGraph().onFilterToggle({ currentTarget: anchor }));
  act(() => h.toolbar.onApplyCompareExtraFilters([COMPARE_ROW]));
  await waitFor(() =>
    expect(compareGraph().filters).toContainEqual(COMPARE_ROW),
  );
};

describe("Users Compare graph", () => {
  beforeEach(() => {
    h.url = {};
    h.graphs = {};
    h.toolbar = null;
    h.saveView.mockClear();
    h.share.mockClear();
    storedValues.clear();
  });

  it("draws a Compare Graph of the same metric over its own date window", async () => {
    h.url = { userShowCompare: true, userCompareDateFilter: COMPARE_WINDOW };
    renderAt(PROJECT_USERS);
    await waitFor(() =>
      expect(graphLabels()).toEqual(["User Metrics", "Compare Graph"]),
    );
    const primary = primaryGraph();
    const compare = compareGraph();

    expect(compare).toMatchObject({
      graphEndpoint: primary.graphEndpoint,
      defaultMetric: primary.defaultMetric,
      trafficLabel: "users",
      showDateFilter: true,
      hasActiveFilter: false,
    });
    expect(compare.lineColorOverride).toBeTruthy();
    expect(compare.barColorOverride).toBeTruthy();
    expect(primary.lineColorOverride).toBeUndefined();
    // The compare window comes from userCompareDateFilter; the primary keeps
    // the toolbar's. The toolbar hides its date and Filter in compare mode,
    // so each graph carries its own date and Filter buttons.
    expect(compare.dateFilter).toEqual(COMPARE_WINDOW);
    expect(drawnWindow(compare)).toEqual(COMPARE_WINDOW.dateFilter);
    expect(primary.dateFilter.dateOption).toBe("7D");
    expect(drawnWindow(primary)).not.toEqual(COMPARE_WINDOW.dateFilter);
    expect(primary.showDateFilter).toBe(true);
    expect(primary.onFilterToggle).toEqual(expect.any(Function));
    expect(compare.onFilterToggle).toEqual(expect.any(Function));

    // Moving the compare window leaves the primary where it was.
    const primaryWindow = drawnWindow(primary);
    const next = {
      dateFilter: ["2026-08-01T00:00:00.000Z", "2026-08-02T00:00:00.000Z"],
      dateOption: "Custom",
    };
    act(() => compare.setDateFilter(next));
    await waitFor(() => expect(compareGraph().dateFilter).toEqual(next));
    expect(drawnWindow(compareGraph())).toEqual(next.dateFilter);
    expect(drawnWindow(primaryGraph())).toEqual(primaryWindow);
  });

  it("shows only the primary graph until Compare is ticked", async () => {
    renderAt(PROJECT_USERS);
    await waitFor(() => expect(graphLabels()).toEqual(["User Metrics"]));
    expect(h.toolbar.isCompareActive).toBe(false);
    expect(primaryGraph().showDateFilter).toBeFalsy();
    expect(primaryGraph().setDateFilter).toBeUndefined();
    expect(primaryGraph().onFilterToggle).toBeUndefined();

    act(() => h.toolbar.onCompareToggle());
    await waitFor(() =>
      expect(graphLabels()).toEqual(["User Metrics", "Compare Graph"]),
    );
    // It opens on the primary's default window, as the Trace tab's does.
    expect(compareGraph().dateFilter).toEqual(primaryGraph().dateFilter);
  });

  it("gives the primary graph a Filter pill that opens the primary filters", async () => {
    // The toolbar hides its own Filter button in compare mode.
    h.url = { userShowCompare: true };
    renderAt(PROJECT_USERS);
    await waitFor(() =>
      expect(primaryGraph()?.onFilterToggle).toEqual(expect.any(Function)),
    );
    expect(primaryGraph().hasActiveFilter).toBe(false);

    const anchor = document.createElement("button");
    act(() => primaryGraph().onFilterToggle({ currentTarget: anchor }));
    expect(h.toolbar.externalFilterAnchor).toBe(anchor);
    expect(h.toolbar).toMatchObject({
      filterTarget: "primary",
      isFilterOpen: true,
      graphFilters: [],
    });
    act(() => h.toolbar.onApplyExtraFilters([PRIMARY_ROW]));
    await waitFor(() =>
      expect(primaryGraph().filters).toContainEqual(PRIMARY_ROW),
    );
    expect(primaryGraph().hasActiveFilter).toBe(true);

    // A second click closes the panel.
    act(() => primaryGraph().onFilterToggle({ currentTarget: anchor }));
    expect(h.toolbar.isFilterOpen).toBe(false);
    expect(h.toolbar.externalFilterAnchor).toBeNull();
  });

  it("sends the filter panel to the graph whose Filter button opened it", async () => {
    h.url = { userShowCompare: true };
    renderAt(PROJECT_USERS);
    await waitFor(() => expect(graphLabels()).toHaveLength(2));
    expect(h.toolbar.filterTarget).toBe("primary");

    // By default the panel edits the primary graph (and the grid).
    act(() => h.toolbar.onApplyExtraFilters([PRIMARY_ROW]));
    await waitFor(() =>
      expect(primaryGraph().filters).toContainEqual(PRIMARY_ROW),
    );
    expect(primaryGraph().hasActiveFilter).toBe(true);
    expect(compareGraph().filters).not.toContainEqual(PRIMARY_ROW);

    // The compare graph's Filter button opens the panel on its own filters.
    const anchor = document.createElement("button");
    act(() => compareGraph().onFilterToggle({ currentTarget: anchor }));
    expect(h.toolbar.externalFilterAnchor).toBe(anchor);
    expect(h.toolbar).toMatchObject({
      filterTarget: "compare",
      isFilterOpen: true,
      graphFilters: [],
    });
    act(() => h.toolbar.onApplyCompareExtraFilters([COMPARE_ROW]));
    await waitFor(() =>
      expect(compareGraph().filters).toContainEqual(COMPARE_ROW),
    );
    expect(compareGraph().hasActiveFilter).toBe(true);
    expect(drawnWindow(compareGraph())).toEqual(
      drawnWindow({ dateFilter: compareGraph().dateFilter }),
    );
    expect(primaryGraph().filters).not.toContainEqual(COMPARE_ROW);
    expect(h.toolbar.graphFilters).toEqual([COMPARE_ROW]);

    // With Compare off the panel is the primary's again.
    act(() => h.toolbar.onCompareToggle());
    await waitFor(() => expect(graphLabels()).toEqual(["User Metrics"]));
    expect(h.toolbar.filterTarget).toBe("primary");
    expect(h.toolbar.graphFilters).toEqual([PRIMARY_ROW]);

    // Back on, closing from the toolbar hands the panel back to the primary.
    act(() => h.toolbar.onCompareToggle());
    await waitFor(() => expect(graphLabels()).toHaveLength(2));
    expect(h.toolbar.filterTarget).toBe("compare");
    act(() => h.toolbar.onFilterToggle());
    expect(h.toolbar.externalFilterAnchor).toBeNull();
    expect(h.toolbar).toMatchObject({
      filterTarget: "primary",
      isFilterOpen: false,
      graphFilters: [PRIMARY_ROW],
    });
  });

  it("puts the compare window and filters back on Reset view", async () => {
    h.url = { userShowCompare: true, userCompareDateFilter: COMPARE_WINDOW };
    renderAt(PROJECT_USERS);
    await waitFor(() => expect(graphLabels()).toHaveLength(2));
    act(() => h.toolbar.onApplyCompareExtraFilters([COMPARE_ROW]));
    await waitFor(() =>
      expect(compareGraph().filters).toContainEqual(COMPARE_ROW),
    );

    act(() => h.toolbar.onResetView());
    await waitFor(() => expect(graphLabels()).toEqual(["User Metrics"]));
    act(() => h.toolbar.onCompareToggle());
    await waitFor(() => expect(graphLabels()).toHaveLength(2));
    const compare = compareGraph();
    expect(compare.dateFilter.dateOption).toBe("7D");
    expect(drawnWindow(compare)).not.toEqual(COMPARE_WINDOW.dateFilter);
    expect(compare.filters).not.toContainEqual(COMPARE_ROW);
    expect(compare.hasActiveFilter).toBe(false);
    expect(h.toolbar.filterTarget).toBe("primary");
  });

  it.each([
    [
      "another saved view",
      "second",
      { extra_filters: [PRIMARY_ROW], display: {} },
      [{ column_id: "user_id", filter_config: { filter_value: "Alice" } }],
    ],
    ["the default view", null, null, []],
  ])(
    "starts the compare graph clean after switching to %s",
    async (_name, nextId, nextView, panelFilters) => {
      h.url = { userShowCompare: true };
      renderAt(PROJECT_USERS);
      await waitFor(() => expect(graphLabels()).toHaveLength(2));
      await setCompareFilterOnSavedView("first");

      act(() => h.toolbar.onCompareToggle());
      await waitFor(() => expect(graphLabels()).toEqual(["User Metrics"]));
      if (nextId) openSavedView(nextId, nextView);
      else act(() => h.openView(null));
      act(() => h.toolbar.onCompareToggle());
      await waitFor(() => expect(graphLabels()).toHaveLength(2));

      // Users saves no compare state: nothing carries over, and the panel
      // edits the primary filters the new view brought.
      expect(compareGraph().filters).not.toContainEqual(COMPARE_ROW);
      expect(compareGraph().hasActiveFilter).toBe(false);
      expect(h.toolbar.filterTarget).toBe("primary");
      expect(h.toolbar.graphFilters).toMatchObject(panelFilters);
      expect(h.toolbar.graphFilters).toHaveLength(panelFilters.length);
    },
  );

  it.each([
    ["Save view", "onSaveView", h.saveView],
    ["Share with project", "onSetDefaultView", h.share],
  ])(
    "keeps the compare filter when %s refreshes the open view",
    async (_name, action, request) => {
      h.url = { userShowCompare: true };
      renderAt(PROJECT_USERS);
      await waitFor(() => expect(graphLabels()).toHaveLength(2));
      await setCompareFilterOnSavedView("saved");

      // The same view takes a refreshed baseline (its row height changes).
      act(() => h.toolbar[action]());
      expect(request).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(h.toolbar.cellHeight).toBe("Large"));

      // Still the same view: its compare filter and target stay.
      expect(compareGraph().filters).toContainEqual(COMPARE_ROW);
      expect(compareGraph().hasActiveFilter).toBe(true);
      expect(h.toolbar.filterTarget).toBe("compare");
      expect(h.toolbar.graphFilters).toEqual([COMPARE_ROW]);
    },
  );

  it("draws no graph on the standalone Users page, Compare or not", async () => {
    h.url = { userShowCompare: true, userCompareDateFilter: COMPARE_WINDOW };
    renderAt("/dashboard/users");
    expect(await screen.findByTestId("users-grid")).toBeInTheDocument();
    // Let a lazily loaded graph resolve, were one mounted.
    await act(async () => {
      await import("../../LLMTracing/GraphSection/PrimaryGraph");
    });
    expect(screen.queryAllByTestId("users-graph")).toHaveLength(0);
    expect(h.graphs).toEqual({});
    // No compare graph here, so the panel only ever edits the primary.
    expect(h.toolbar.filterTarget).toBe("primary");
  });
});
