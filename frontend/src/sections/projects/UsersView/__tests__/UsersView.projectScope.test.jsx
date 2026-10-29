import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  render,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";

// TH-5037: the Users API returns one row per user *within a project* (one
// EndUser each). The page must make that scope visible instead of reading as
// a de-duplicated list of people. The real UsersView, UsersGrid, users store
// and column config run here; only AG Grid, the transport and the
// surrounding page chrome are replaced.
const storedValues = new Map();
vi.stubGlobal("localStorage", {
  clear: () => storedValues.clear(),
  getItem: (key) => storedValues.get(key) ?? null,
  removeItem: (key) => storedValues.delete(key),
  setItem: (key, value) => storedValues.set(key, String(value)),
});

const { getMock, gridState, header } = vi.hoisted(() => ({
  getMock: vi.fn(),
  gridState: { props: null, api: null },
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
      gridState.props = props;
      ReactModule.useImperativeHandle(
        ref,
        () => ({
          get api() {
            return gridState.api;
          },
        }),
        [],
      );
      return <div data-testid="users-ag-grid" />;
    },
  );
  return { AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/utils/axios", () => ({
  readQuery: (...args) => getMock(...args),
  default: { get: (...args) => getMock(...args) },
  endpoints: {
    project: {
      getUsersList: () => "/tracer/users/",
      getUsersAggregateGraphData: () => "/tracer/users/graph/",
    },
  },
}));
vi.mock("../../LLMTracing/GraphSection/PrimaryGraph", () => ({
  default: () => null,
}));
vi.mock("../UsersEmptyScreen", () => ({
  default: () => <div>Confirmed empty users</div>,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "test-workspace" }),
}));
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => header,
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return { useUrlState: (_key, initial) => useState(initial) };
});
vi.mock("src/api/project/saved-views", () => ({
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useGetWorkspaceSavedViews: () => ({ data: { custom_views: [] } }),
}));
vi.mock("../../LLMTracing/useLLMTracingFilters", async () => {
  const filters = [];
  return {
    useLLMTracingFilters: (_filters, dateFilter) => ({
      validatedFilters: filters,
      filters,
      setFilters: vi.fn(),
      dateFilter,
      setDateFilter: vi.fn(),
    }),
  };
});
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
vi.mock("../UsersPageTabBar", () => ({ default: () => null }));

import UsersView from "../UsersView";
import UserList from "src/pages/dashboard/projects/UsersList";

// Synthetic fixture: one person (`user_123`) active in two projects.
const SUPPORT_BOT = "11111111-1111-4111-8111-111111111111";
const SALES_BOT = "22222222-2222-4222-8222-222222222222";
const sameUserInTwoProjects = [
  {
    user_id: "user_123",
    end_user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    project_id: SUPPORT_BOT,
    project_name: "Support Bot",
    num_traces: 5,
    total_cost: 0.12,
  },
  {
    user_id: "user_123",
    end_user_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    project_id: SALES_BOT,
    project_name: "Sales Bot",
    num_traces: 7,
    total_cost: 0.31,
  },
];

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

const visibleFields = () =>
  (gridState.props?.columnDefs || [])
    .filter((column) => !column.hide)
    .map((column) => column.field);

const projectColumn = () =>
  (gridState.props?.columnDefs || []).find(
    (column) => column.field === "project_name",
  );

describe("Users rows are labelled with their project (TH-5037)", () => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.props = null;
    gridState.api = null;
    storedValues.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a Project column next to User ID on the cross-project Users page", async () => {
    getMock.mockResolvedValue(usersResponse(sameUserInTwoProjects));
    render(
      <HelmetProvider>
        <UsersView />
      </HelmetProvider>,
    );
    await waitFor(() => expect(projectColumn()).toBeDefined());

    expect(projectColumn().headerName).toBe("Project");
    expect(visibleFields().slice(0, 2)).toEqual(["user_id", "project_name"]);

    const params = makeGridParams();
    gridState.api = params.api;
    await act(async () => {
      await gridState.props.serverSideDatasource.getRows(params);
    });
    const { rowData } = params.success.mock.calls[0][0];
    expect(rowData.map((row) => row.user_id)).toEqual(["user_123", "user_123"]);
    // Same user, two projects: two distinct grid rows ...
    const rowIds = rowData.map((data) => gridState.props.getRowId({ data }));
    expect(new Set(rowIds).size).toBe(2);

    // ... and each one now says which project it belongs to.
    const ProjectCell = projectColumn().cellRenderer;
    render(
      <>
        {rowData.map((row) => (
          <ProjectCell key={row.end_user_id} data={row} />
        ))}
      </>,
    );
    expect(screen.getByText("Support Bot")).toBeVisible();
    expect(screen.getByText("Sales Bot")).toBeVisible();
  });

  it("keeps a row's scope visible when its project name is unavailable", async () => {
    render(
      <HelmetProvider>
        <UsersView />
      </HelmetProvider>,
    );
    await waitFor(() => expect(projectColumn()).toBeDefined());
    const ProjectCell = projectColumn().cellRenderer;
    render(
      <ProjectCell
        data={{
          user_id: "user_123",
          project_id: SALES_BOT,
          project_name: null,
        }}
      />,
    );
    expect(screen.getByText("Unknown project")).toBeVisible();
  });

  it("does not add a Project column inside a single project's Users tab", async () => {
    renderWithRouter(
      <HelmetProvider>
        <Routes>
          <Route
            path="/dashboard/observe/:observeId/users"
            element={<UsersView />}
          />
        </Routes>
      </HelmetProvider>,
      { route: `/dashboard/observe/${SUPPORT_BOT}/users` },
    );
    await waitFor(() => expect(visibleFields()).toContain("user_id"));
    expect(projectColumn()).toBeUndefined();
  });

  it("describes the page as per-user-per-project, not de-duplicated users", () => {
    renderWithRouter(
      <HelmetProvider>
        <UserList />
      </HelmetProvider>,
      { route: "/dashboard/users" },
    );
    expect(screen.getByText("One row per user per project")).toBeVisible();
    expect(
      screen.queryByText("All users across your projects"),
    ).not.toBeInTheDocument();
  });
});
