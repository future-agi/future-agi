/* eslint-disable react/prop-types */
// Sessions draws a Compare graph below its primary graph, as Traces does:
// the same session graph with its own date window (URL key
// sessionCompareDateFilter) and its own filters, saved with the view and
// cleared by Reset. With compare on, the toolbar hides its date and Filter,
// so each graph carries its own. A user's page draws no graphs, so it has
// nothing to compare.
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { endpoints } from "src/utils/axios";
import SessionsView from "../Sessions-view";

const h = vi.hoisted(() => ({
  toolbar: null,
  grid: null,
  graphs: {},
  hookArgs: {},
  routeParams: { observeId: "project-1" },
  selection: { toggledNodes: [], selectAll: false, totalRowCount: 0 },
  navigate: vi.fn(),
  save: vi.fn(),
  header: {
    activeViewConfig: null,
    setHeaderConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    registerGetViewConfig: vi.fn(),
  },
}));

vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => h.routeParams,
  useNavigate: () => h.navigate,
}));
vi.mock("src/hooks/useRestrictSimulateToEnvironments", () => ({
  useRestrictSimulateToEnvironments: () => false,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return { useUrlState: (_key, initial) => useState(initial) };
});
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => h.header,
}));
// Each call records its URL keys. validatedFilters carries the call's own
// window, so a graph's filters show which window it queries. A window in
// the URL (a shared link) is read on the first render, as useUrlState does.
vi.mock("../../LLMTracing/useLLMTracingFilters", async () => {
  const { useMemo, useState } = await import("react");
  return {
    useLLMTracingFilters: (initialFilters, initialDate, filterKey, dateKey) => {
      h.hookArgs[dateKey] = { filterKey, initialDate };
      const [filters, setFilters] = useState(initialFilters);
      const [dateFilter, setDateFilter] = useState(() => {
        const linked = new URLSearchParams(window.location.search).get(dateKey);
        return linked === null ? initialDate : JSON.parse(linked);
      });
      const validatedFilters = useMemo(
        () => [
          {
            column_id: "created_at",
            filter_config: {
              filter_type: "datetime",
              filter_op: "between",
              filter_value: dateFilter?.dateFilter,
            },
          },
        ],
        [dateFilter],
      );
      return {
        filters,
        setFilters,
        dateFilter,
        setDateFilter,
        validatedFilters,
      };
    },
  };
});
vi.mock("../../LLMTracing/ObserveToolbar", () => ({
  default: (props) => {
    h.toolbar = props;
    return null;
  },
}));
// The real graph loads ApexCharts; this one records its props by label.
vi.mock("../../LLMTracing/GraphSection/PrimaryGraph", () => ({
  default: (props) => {
    h.graphs[props.graphLabel] = props;
    return <div data-testid="session-graph">{props.graphLabel}</div>;
  },
}));
vi.mock("../Session-grid", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function MockSessionGrid(props, _ref) {
      h.grid = props;
      return null;
    }),
  };
});
vi.mock("../../UsersView/useProjectFilterField", () => ({
  default: () => null,
}));
vi.mock("../../LLMTracing/FilterChips", () => ({ default: () => null }));
vi.mock("../../LLMTracing/SelectAllBanner", () => ({ default: () => null }));
vi.mock("../../LLMTracing/CustomColumnDialog", () => ({ default: () => null }));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);
vi.mock("../../LLMTracing/useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: [],
    inventoryControlProps: {},
  }),
}));
vi.mock("../ReplaySessions/store", () => ({
  useReplaySessionsStoreShallow: (selector) =>
    selector({
      openReplaySessionDrawer: {},
      setCreatedReplay: vi.fn(),
      setReplayType: vi.fn(),
      setOpenReplaySessionDrawer: vi.fn(),
    }),
  useSessionsGridStore: {
    setState: (state) => Object.assign(h.selection, state),
  },
  useSessionsGridStoreShallow: (selector) => selector(h.selection),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    default: {
      ...original.default,
      post: () => Promise.resolve({ data: {} }),
    },
  };
});
vi.mock("src/api/project/replay-sessions", () => ({
  useCreateReplaySessions: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useUpdateSavedView: () => ({ mutate: h.save }),
  useUpdateWorkspaceSavedView: () => ({ mutate: h.save }),
  useSetProjectDefaultView: () => vi.fn(),
}));

const USER_MODE_COMPARE_REASON =
  "Compare graph needs a project. Open the sessions from their project.";

const custom = (bounds) => ({ dateOption: "Custom", dateFilter: bounds });
const PRIMARY_WINDOW = custom(["2026-10-01 00:00:00", "2026-10-02 23:59:59"]);
const COMPARE_WINDOW = custom(["2026-09-01 00:00:00", "2026-09-02 23:59:59"]);
const EDITED_WINDOW = custom(["2026-08-01 00:00:00", "2026-08-02 23:59:59"]);
const LINKED_WINDOW = custom(["2026-07-01 00:00:00", "2026-07-02 23:59:59"]);
const aliceFilter = {
  column_id: "user_id",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "Alice",
  },
};

const mount = (props = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <React.Suspense fallback={null}>
        <SessionsView {...props} />
      </React.Suspense>
    </QueryClientProvider>,
  );

// The graphs are lazy-loaded; give their first import time.
const findGraphs = (count) =>
  waitFor(
    () => expect(screen.getAllByTestId("session-graph")).toHaveLength(count),
    { timeout: 10000 },
  );
const graph = (label) => h.graphs[label];
const turnCompareOn = async () => {
  await waitFor(() => expect(h.toolbar).not.toBeNull());
  act(() => h.toolbar.onCompareToggle());
  await waitFor(() => expect(h.toolbar.isCompareActive).toBe(true));
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  window.history.replaceState({}, "", "/?tab=sessions");
  h.header.activeViewConfig = null;
  h.routeParams = { observeId: "project-1" };
  h.selection = { toggledNodes: [], selectAll: false, totalRowCount: 0 };
  h.toolbar = null;
  h.grid = null;
  h.graphs = {};
  h.hookArgs = {};
});
afterEach(cleanup);

describe("Sessions Compare graph", () => {
  it("draws a second session graph with its own date window when compare is on", async () => {
    mount();
    await turnCompareOn();
    await findGraphs(2);

    const primary = graph("Session Metrics");
    const compare = graph("Compare Graph");
    expect(compare).toMatchObject({
      graphEndpoint: endpoints.project.getSessionGraphData(),
      defaultMetric: "latency",
      trafficLabel: "sessions",
      showDateFilter: true,
      hasActiveFilter: false,
      // Traces' Compare graph colours (light theme).
      lineColorOverride: "rgba(230, 120, 100, 0.70)",
      barColorOverride: "rgba(230, 170, 147, 0.25)",
    });
    // The toolbar hides its date and Filter in compare mode, so the primary
    // graph carries them.
    expect(primary).toMatchObject({
      showDateFilter: true,
      hasActiveFilter: false,
    });
    expect(primary.setDateFilter).toEqual(expect.any(Function));
    expect(primary.onFilterToggle).toEqual(expect.any(Function));

    // Its own URL key, defaulting as the primary window does.
    expect(h.hookArgs.sessionCompareDateFilter).toEqual({
      filterKey: "sessionCompareFilter",
      initialDate: h.hookArgs.sessionDateFilter.initialDate,
    });
    expect(compare.dateFilter).toEqual(primary.dateFilter);

    act(() => compare.setDateFilter(COMPARE_WINDOW));

    await waitFor(() =>
      expect(graph("Compare Graph").dateFilter).toEqual(COMPARE_WINDOW),
    );
    expect(graph("Compare Graph").filters).toContainEqual(
      expect.objectContaining({
        column_id: "created_at",
        filter_config: expect.objectContaining({
          filter_value: COMPARE_WINDOW.dateFilter,
        }),
      }),
    );
    // The primary graph, the toolbar and the list keep their own window.
    expect(graph("Session Metrics").dateFilter).toEqual(primary.dateFilter);
    expect(h.toolbar.dateFilter).toEqual(primary.dateFilter);
    expect(JSON.stringify(h.grid.filters)).not.toContain("2026-09-01");
  });

  it("draws only the primary graph with compare off", async () => {
    mount();
    await findGraphs(1);

    expect(screen.getByText("Session Metrics")).toBeInTheDocument();
    expect(screen.queryByText("Compare Graph")).not.toBeInTheDocument();
    // The toolbar keeps the date and Filter; the graph has no setter, so
    // its drag-to-zoom cannot move the list's window.
    const primary = graph("Session Metrics");
    expect(primary.showDateFilter).toBeFalsy();
    expect(primary.setDateFilter).toBeUndefined();
    expect(primary.onFilterToggle).toBeUndefined();
  });

  it("draws no compare graph on a user's page and says why", async () => {
    h.routeParams = { userId: "user-1" };
    mount({ mode: "user", userIdForUserMode: "user-1" });
    await turnCompareOn();

    expect(h.toolbar.compareDisabledReason).toBe(USER_MODE_COMPARE_REASON);
    expect(screen.queryAllByTestId("session-graph")).toHaveLength(0);
    expect(h.graphs).toEqual({});
  });

  it("edits each graph's filters from its own Filter pill", async () => {
    mount();
    await turnCompareOn();
    await findGraphs(2);
    const anchor = document.createElement("button");

    act(() => graph("Compare Graph").onFilterToggle({ currentTarget: anchor }));
    expect(h.toolbar).toMatchObject({
      isFilterOpen: true,
      filterTarget: "compare",
      graphFilters: [],
    });
    expect(h.toolbar.externalFilterAnchor).toBe(anchor);

    act(() => h.toolbar.onApplyCompareExtraFilters([aliceFilter]));

    await waitFor(() =>
      expect(graph("Compare Graph").filters).toContainEqual(aliceFilter),
    );
    expect(graph("Compare Graph").hasActiveFilter).toBe(true);
    expect(h.toolbar.graphFilters).toEqual([aliceFilter]);
    expect(graph("Session Metrics").filters).not.toContainEqual(aliceFilter);
    expect(h.grid.filters).not.toContainEqual(aliceFilter);

    // Closing from the toolbar hands the panel back to the primary filters.
    act(() => h.toolbar.onFilterToggle());
    expect(h.toolbar).toMatchObject({
      isFilterOpen: false,
      filterTarget: "primary",
      graphFilters: [],
    });

    act(() =>
      graph("Session Metrics").onFilterToggle({ currentTarget: anchor }),
    );
    expect(h.toolbar).toMatchObject({
      isFilterOpen: true,
      filterTarget: "primary",
      graphFilters: [],
    });
  });

  it("restores, saves and resets a saved view's compare window and filters", async () => {
    window.history.replaceState({}, "", "/?tab=view-saved");
    h.header.activeViewConfig = {
      extra_filters: [],
      display: {
        cellHeight: "Short",
        showCompare: true,
        dateFilter: PRIMARY_WINDOW,
      },
      compare_date_filter: COMPARE_WINDOW,
      compare_extra_filters: [aliceFilter],
    };
    mount();
    await findGraphs(2);
    await waitFor(() =>
      expect(graph("Compare Graph").dateFilter).toEqual(COMPARE_WINDOW),
    );
    expect(graph("Compare Graph").filters).toContainEqual(aliceFilter);
    expect(graph("Session Metrics").dateFilter).toEqual(PRIMARY_WINDOW);
    await waitFor(() => expect(h.toolbar.canSaveView).toBe(false));

    act(() => graph("Compare Graph").setDateFilter(EDITED_WINDOW));
    await waitFor(() => expect(h.toolbar.canSaveView).toBe(true));
    act(() => h.toolbar.onSaveView());
    expect(h.save).toHaveBeenCalledTimes(1);
    expect(h.save.mock.calls[0][0]).toMatchObject({
      id: "saved",
      config: {
        display: { showCompare: true, dateFilter: PRIMARY_WINDOW },
        compare_date_filter: EDITED_WINDOW,
        compare_extra_filters: [aliceFilter],
      },
    });

    act(() => h.toolbar.onResetView());
    await waitFor(() => expect(h.toolbar.isCompareActive).toBe(false));
    await findGraphs(1);
    act(() => h.toolbar.onCompareToggle());
    await findGraphs(2);
    expect(graph("Compare Graph").dateFilter).toMatchObject({
      dateOption: "7D",
    });
    expect(graph("Compare Graph").filters).not.toContainEqual(aliceFilter);
    expect(graph("Compare Graph").hasActiveFilter).toBe(false);
  });

  it("keeps the default tab's compare window with its display settings", async () => {
    const view = mount();
    await turnCompareOn();
    await findGraphs(2);
    act(() => graph("Compare Graph").setDateFilter(COMPARE_WINDOW));

    await waitFor(() =>
      expect(
        JSON.parse(localStorage.getItem("observe-sessions-display-project-1")),
      ).toMatchObject({
        showCompare: true,
        compare_date_filter: COMPARE_WINDOW,
      }),
    );
    view.unmount();
    h.graphs = {};

    mount();
    await findGraphs(2);
    await waitFor(() =>
      expect(graph("Compare Graph").dateFilter).toEqual(COMPARE_WINDOW),
    );
  });

  // A Compare window in the URL (a shared link) wins over the one stored for
  // the default tab.
  it("keeps a link's compare window on the default tab over the stored one", async () => {
    localStorage.setItem(
      "observe-sessions-display-project-1",
      JSON.stringify({
        showCompare: true,
        compare_date_filter: COMPARE_WINDOW,
      }),
    );
    const link = encodeURIComponent(JSON.stringify(LINKED_WINDOW));
    window.history.replaceState(
      {},
      "",
      `/?tab=sessions&sessionCompareDateFilter=${link}`,
    );
    mount();
    await findGraphs(2);

    await waitFor(() =>
      expect(graph("Compare Graph").dateFilter).toEqual(LINKED_WINDOW),
    );
    // The stored compare flag still applies; the primary window is its own.
    expect(h.toolbar.isCompareActive).toBe(true);
    expect(graph("Session Metrics").dateFilter).toEqual(
      h.hookArgs.sessionDateFilter.initialDate,
    );
  });

  // Guard: a saved view without compare fields replaces the compare
  // filters, so none are left behind from the tab before it.
  it("leaves no compare filters behind on a saved view without compare fields", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const ui = () => (
      <QueryClientProvider client={client}>
        <React.Suspense fallback={null}>
          <SessionsView />
        </React.Suspense>
      </QueryClientProvider>
    );
    const view = render(ui());
    await turnCompareOn();
    await findGraphs(2);
    act(() => h.toolbar.onApplyCompareExtraFilters([aliceFilter]));
    await waitFor(() =>
      expect(graph("Compare Graph").filters).toContainEqual(aliceFilter),
    );

    window.history.replaceState({}, "", "/?tab=view-plain");
    h.header.activeViewConfig = {
      extra_filters: [],
      display: {
        cellHeight: "Short",
        showCompare: true,
        dateFilter: PRIMARY_WINDOW,
      },
    };
    view.rerender(ui());

    await waitFor(() =>
      expect(graph("Session Metrics").dateFilter).toEqual(PRIMARY_WINDOW),
    );
    expect(h.toolbar.isCompareActive).toBe(true);
    expect(graph("Compare Graph").filters).not.toContainEqual(aliceFilter);
    expect(graph("Compare Graph").hasActiveFilter).toBe(false);
  });

  // Guard: a view saved without a compare window of its own is not edited
  // by compare's default window.
  it("keeps a view without compare_date_filter clean while compare is on", async () => {
    window.history.replaceState({}, "", "/?tab=view-saved");
    h.header.activeViewConfig = {
      extra_filters: [],
      display: {
        cellHeight: "Short",
        showCompare: true,
        dateFilter: PRIMARY_WINDOW,
      },
    };
    mount();
    await findGraphs(2);

    await waitFor(() => expect(h.toolbar.canSaveView).toBe(false));
    expect(graph("Compare Graph").dateFilter).toEqual(
      h.hookArgs.sessionCompareDateFilter.initialDate,
    );
    expect(h.toolbar.isCompareActive).toBe(true);
    expect(h.toolbar.canSaveView).toBe(false);
  });
});
