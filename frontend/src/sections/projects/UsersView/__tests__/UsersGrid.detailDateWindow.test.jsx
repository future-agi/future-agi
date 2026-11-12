import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderWithRouter } from "src/utils/test-utils";

// A row click opens the user's detail page. That page defaults to Today, so
// the click must carry the list's own date window or a user active earlier
// in the week opens on empty Sessions / Trace grids.
const { gridState, navigateMock, storeState } = vi.hoisted(() => ({
  gridState: { props: null },
  navigateMock: vi.fn(),
  storeState: {
    setGridApi: vi.fn(),
    searchQuery: "",
    selectedAll: false,
    selectedRowsData: [],
    setSelectedAll: vi.fn(),
    setSelectedRowsData: vi.fn(),
    clearSelection: vi.fn(),
    columns: [],
    setColumns: vi.fn(),
    filters: [],
  },
}));

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, _ref) {
      gridState.props = props;
      return <div data-testid="ag-grid" />;
    },
  );
  return { AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("../Store/usersStore", () => {
  const useUsersStore = () => storeState;
  useUsersStore.setState = vi.fn();
  return { default: useUsersStore };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("../common", () => ({
  getUsersColumnConfig: () => [
    { field: "user_id", headerName: "User Id" },
    { field: "last_active", headerName: "Last Active" },
  ],
  userTraceRowHeightMapping: { Short: { height: 40 } },
  buildUsersRequestFilters: (filters) => filters,
}));
vi.mock("../../LLMTracing/common", () => ({
  mergeCellStyle: () => () => ({}),
}));
vi.mock("src/sections/project-detail/CompareDrawer/NoRowsOverlay", () => ({
  default: (content) => content,
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigateMock,
  useParams: () => ({ observeId: "proj-1" }),
}));
vi.mock("src/utils/axios", () => ({
  readQuery: vi.fn(),
  default: { get: vi.fn() },
  endpoints: { project: { getUsersList: () => "/projects/users/" } },
}));

import UsersGrid from "../UsersGrid";

const renderGrid = (search = "") => {
  renderWithRouter(
    <UsersGrid
      hasActiveFilter={false}
      setHasData={vi.fn()}
      setIsLoading={vi.fn()}
      setSearchState={vi.fn()}
      cellHeight="Short"
    />,
    { route: `/dashboard/observe/proj-1/users${search}` },
  );
};

const clickUserRow = (userId) => {
  act(() => {
    gridState.props.onCellClicked({
      colDef: { colId: "user_id" },
      data: { user_id: userId },
      node: { isSelected: () => false, setSelected: vi.fn() },
    });
  });
  expect(navigateMock).toHaveBeenCalledTimes(1);
  return new URL(navigateMock.mock.calls[0][0], "http://localhost");
};

describe("UsersGrid row click opens the user on the list's date window", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    gridState.props = null;
  });

  it("forwards the list's selected window to both detail tabs", () => {
    const listWindow = {
      dateFilter: ["2026-10-01 00:00:00", "2026-10-31 23:59:59"],
      dateOption: "Custom",
    };
    renderGrid(
      `?${new URLSearchParams({ userDateFilter: JSON.stringify(listWindow) })}`,
    );

    const target = clickUserRow("alice");

    expect(target.pathname).toBe("/dashboard/users/alice");
    expect(target.searchParams.get("sessionDateFilter")).toBe(
      JSON.stringify(listWindow),
    );
    expect(target.searchParams.get("primaryTraceDateFilter")).toBe(
      JSON.stringify(listWindow),
    );
  });

  it("forwards the list's default Past 7D window when none is in the URL", () => {
    renderGrid();

    const target = clickUserRow("alice");

    expect(target.pathname).toBe("/dashboard/users/alice");
    const sessionWindow = target.searchParams.get("sessionDateFilter");
    expect(sessionWindow).not.toBeNull();
    expect(JSON.parse(sessionWindow).dateOption).toBe("7D");
    expect(JSON.parse(sessionWindow).dateFilter).toHaveLength(2);
    expect(target.searchParams.get("primaryTraceDateFilter")).toBe(
      sessionWindow,
    );
  });

  it("does not navigate from the actions column", () => {
    renderGrid();

    act(() => {
      gridState.props.onCellClicked({
        colDef: { colId: "actions" },
        data: { user_id: "alice" },
      });
    });

    expect(navigateMock).not.toHaveBeenCalled();
  });
});
