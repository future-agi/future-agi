import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderWithRouter, screen, waitFor } from "src/utils/test-utils";

// A new project's Users tab: the list read always carries the toolbar's date
// window, so an empty first page cannot say whether user IDs were ever sent.
// The view then asks the project's end-user list (all time) for one user:
// none at all shows the setup screen; users outside the window keep the
// grid's range-empty state; users in the window need no check. The real
// UsersView, UsersGrid and UsersEmptyScreen run here; AG Grid, the
// transport and the page chrome are replaced.
const storedValues = new Map();
vi.stubGlobal("localStorage", {
  clear: () => storedValues.clear(),
  getItem: (key) => storedValues.get(key) ?? null,
  removeItem: (key) => storedValues.delete(key),
  setItem: (key, value) => storedValues.set(key, String(value)),
});

const h = vi.hoisted(() => ({
  read: vi.fn(),
  get: vi.fn(),
  gridState: { props: null, api: null },
  filterState: { validatedFilters: [] },
  header: {
    setHeaderConfig: () => {},
    setActiveViewConfig: () => {},
    registerGetViewConfig: () => {},
  },
}));

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, ref) {
      h.gridState.props = props;
      ReactModule.useImperativeHandle(
        ref,
        () => ({
          get api() {
            return h.gridState.api;
          },
        }),
        [],
      );
      // eslint-disable-next-line react/prop-types
      const overlay = props.noRowsOverlayComponent?.();
      return <div data-testid="users-ag-grid">{overlay}</div>;
    },
  );
  return { AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/utils/axios", () => ({
  readQuery: (...args) => h.read(...args),
  default: { get: (...args) => h.get(...args) },
  endpoints: {
    project: {
      getUsersList: () => "/tracer/users/",
      getUsersAggregateGraphData: () => "/tracer/users/graph/",
      getUserExampleCode: () => "/tracer/users/get_code_example/",
    },
    dashboard: { filterValues: "/tracer/dashboard/filter_values/" },
  },
}));
vi.mock("../common", () => ({
  getUsersColumnConfig: () => [
    { field: "user_id", headerName: "User ID" },
    { field: "last_active", headerName: "Last Active" },
  ],
  userTraceRowHeightMapping: { Short: { height: 40 } },
  buildUsersRequestFilters: (filters) => filters || [],
  userDefaultFilter: {},
}));
vi.mock("../../LLMTracing/common", () => ({
  mergeCellStyle: () => () => ({}),
}));
vi.mock("src/sections/project-detail/CompareDrawer/NoRowsOverlay", () => ({
  default: (content) => content,
}));
// Show each setup step's title and what each code block holds.
vi.mock("src/sections/project/NewProject/InstructionCodeCopy", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ text, language }) => <pre data-language={language}>{text}</pre>,
}));
vi.mock("src/sections/project/NewProject/InstructionTitle", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ title }) => <h6>{title}</h6>,
}));
vi.mock("../../LLMTracing/GraphSection/PrimaryGraph", () => ({
  default: () => null,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "test-workspace" }),
}));
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => h.header,
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return { useUrlState: (_key, initial) => useState(initial) };
});
vi.mock("src/api/project/saved-views", () => ({
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useSetProjectDefaultView: () => vi.fn(),
}));
// Production filters always end with the toolbar's created_at window.
vi.mock("../../LLMTracing/useLLMTracingFilters", () => ({
  useLLMTracingFilters: (_filters, defaultDateFilter) => ({
    validatedFilters: h.filterState.validatedFilters,
    filters: [],
    setFilters: vi.fn(),
    dateFilter: defaultDateFilter,
    setDateFilter: vi.fn(),
  }),
}));
vi.mock("../../LLMTracing/useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: [],
    inventoryControlProps: {},
  }),
}));
vi.mock("../../LLMTracing/ObserveToolbar", () => ({ default: () => null }));
vi.mock("../../LLMTracing/FilterChips", () => ({ default: () => null }));
vi.mock("../../LLMTracing/CustomColumnDialog", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);

import UsersView from "../UsersView";
import useUsersStore from "../Store/usersStore";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const USERS_LIST = "/tracer/users/";
const FILTER_VALUES = "/tracer/dashboard/filter_values/";
const PYTHON_SNIPPET = [
  "import openai",
  "from fi_instrumentation import FITracer, register, using_attributes",
].join("\n");

const usersResponse = (rows) => ({
  data: {
    result: {
      table: rows,
      total_count: rows.length,
      count_is_lower_bound: false,
      total_count_is_lower_bound: false,
      has_more: false,
      next_cursor: null,
      query_complete: true,
      query_status: "complete",
    },
  },
});
const userRow = {
  user_id: "user_123",
  end_user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  project_id: PROJECT,
  num_traces: 5,
};
const knownUser = { value: "user_123", label: "user_123" };

// Answers the list read with `windowRows` and the project's end-user list
// with `projectUsers` (an Error rejects that read).
const answerReads = ({ windowRows, projectUsers }) =>
  h.read.mockImplementation((url) => {
    if (url === USERS_LIST) return Promise.resolve(usersResponse(windowRows));
    if (url !== FILTER_VALUES) return Promise.reject(new Error(url));
    if (projectUsers instanceof Error) return Promise.reject(projectUsers);
    return Promise.resolve({
      data: { result: { values: projectUsers, has_more: false } },
    });
  });
const projectUserChecks = () =>
  h.read.mock.calls.filter(([url]) => url === FILTER_VALUES);

const makeGridParams = () => {
  let context = {};
  return {
    request: { startRow: 0, endRow: 25, sortModel: [] },
    api: {
      hideOverlay: vi.fn(),
      showNoRowsOverlay: vi.fn(),
      applyColumnState: vi.fn(),
      deselectAll: vi.fn(),
      getGridOption: vi.fn(() => context),
      setGridOption: vi.fn((key, value) => {
        if (key === "context") context = value;
      }),
      refreshServerSide: vi.fn(),
      retryServerSideLoads: vi.fn(),
    },
    success: vi.fn(),
    fail: vi.fn(),
  };
};

// Opens the project's Users tab and lets the grid read its first page.
const openUsersTab = async () => {
  renderWithRouter(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <HelmetProvider>
        <Routes>
          <Route
            path="/dashboard/observe/:observeId/users"
            element={<UsersView />}
          />
        </Routes>
      </HelmetProvider>
    </QueryClientProvider>,
    { route: `/dashboard/observe/${PROJECT}/users` },
  );
  await waitFor(() => expect(h.gridState.props).not.toBeNull());
  const params = makeGridParams();
  h.gridState.api = params.api;
  await act(async () => {
    await h.gridState.props.serverSideDatasource.getRows(params);
  });
  return params;
};

const usersGrid = () => screen.getByTestId("users-ag-grid");

describe("A project's Users tab before and after user IDs arrive", () => {
  beforeEach(() => {
    h.read.mockReset();
    h.get.mockReset();
    h.get.mockResolvedValue({ data: { result: PYTHON_SNIPPET } });
    h.gridState.props = null;
    h.gridState.api = null;
    h.filterState.validatedFilters = [
      {
        column_id: "created_at",
        filter_config: {
          filter_type: "datetime",
          filter_op: "between",
          filter_value: [
            "2026-11-07T00:00:00.000Z",
            "2026-11-15T00:00:00.000Z",
          ],
        },
      },
    ];
    storedValues.clear();
    useUsersStore.setState({ searchQuery: "", sortParams: [] });
  });

  it("shows the setup screen when the project has no user data at all", async () => {
    answerReads({ windowRows: [], projectUsers: [] });
    await openUsersTab();

    expect(
      await screen.findByRole("heading", { name: "Set up telemetry" }),
    ).toBeVisible();
    const code = await screen.findByText(/from fi_instrumentation import/);
    expect(code.tagName).toBe("PRE");
    expect(code).toHaveAttribute("data-language", "python");
    expect(usersGrid()).not.toBeVisible();

    // One cheap all-time question: does this project have any end user?
    expect(projectUserChecks()).toHaveLength(1);
    expect(projectUserChecks()[0][1].params).toEqual({
      metric_name: "user_id",
      metric_type: "system_metric",
      project_ids: PROJECT,
      source: "traces",
      page_size: 1,
    });
  });

  it("keeps the range-empty state when the project's users are outside the window", async () => {
    answerReads({ windowRows: [], projectUsers: [knownUser] });
    await openUsersTab();
    await waitFor(() => expect(projectUserChecks()).toHaveLength(1));
    await act(async () => {});

    expect(usersGrid()).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "No users for Past 7D" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Set up telemetry" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the range-empty state when the check cannot answer", async () => {
    answerReads({ windowRows: [], projectUsers: new Error("timed out") });
    await openUsersTab();
    await waitFor(() => expect(projectUserChecks()).toHaveLength(1));
    await act(async () => {});

    expect(usersGrid()).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "No users for Past 7D" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Set up telemetry" }),
    ).not.toBeInTheDocument();
  });

  it("shows the grid, with no check, when users are in the window", async () => {
    answerReads({ windowRows: [userRow], projectUsers: [] });
    const params = await openUsersTab();

    const { rowData } = params.success.mock.calls[0][0];
    expect(rowData.map((row) => row.user_id)).toEqual(["user_123"]);
    expect(usersGrid()).toBeVisible();
    expect(projectUserChecks()).toHaveLength(0);
    expect(
      screen.queryByRole("heading", { name: "Set up telemetry" }),
    ).not.toBeInTheDocument();
  });
});
