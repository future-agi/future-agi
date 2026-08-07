import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "src/utils/test-utils";

const { getMock, validated, storeState } = vi.hoisted(() => {
  const validated = [
    {
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: ["2026-03-01T00:00:00.000Z", "2026-06-01T00:00:00.000Z"],
      },
    },
  ];
  return {
    getMock: vi.fn(),
    validated,
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
      filters: [{ column_id: "created_at" }],
    },
  };
});

// Server-side AG Grid stub: fire getRows once on mount with a minimal params.
function MockAgGridReact({ serverSideDatasource }) {
  React.useEffect(() => {
    serverSideDatasource?.getRows?.({
      request: { startRow: 0, endRow: 100, sortModel: [] },
      api: {
        hideOverlay: vi.fn(),
        showNoRowsOverlay: vi.fn(),
        applyColumnState: vi.fn(),
        getGridOption: () => ({}),
        setGridOption: vi.fn(),
      },
      success: vi.fn(),
      fail: vi.fn(),
    });
  }, [serverSideDatasource]);
  return <div data-testid="ag-grid" />;
}

MockAgGridReact.propTypes = {
  serverSideDatasource: PropTypes.object,
};

vi.mock("ag-grid-react", () => ({ AgGridReact: MockAgGridReact }));
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("../Store/usersStore", () => {
  const useUsersStore = () => storeState;
  // Zustand store exposes a static setState; the grid mirrors sortParams into
  // it on each fetch, so the mock must provide it or getRows throws.
  useUsersStore.setState = () => {};
  return { default: useUsersStore };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (v) => v }));
vi.mock("../common", () => ({
  getUsersColumnConfig: () => [],
  userTraceRowHeightMapping: { Short: { height: 40 } },
  buildUsersRequestFilters: () => validated,
}));
vi.mock("../../LLMTracing/common", () => ({
  mergeCellStyle: () => () => ({}),
}));
vi.mock("src/sections/project-detail/CompareDrawer/NoRowsOverlay", () => ({
  default: () => null,
}));
vi.mock("react-router", async (orig) => ({
  ...(await orig()),
  useNavigate: () => vi.fn(),
  useParams: () => ({ observeId: "proj-1" }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => getMock(...args) },
  endpoints: { project: { getUsersList: () => "/projects/users/" } },
}));

import UsersGrid from "../UsersGrid";

describe("UsersGrid first request (TH-6081)", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue({
      data: { result: { table: [], total_count: 0 } },
    });
  });

  it("issues the first getUsersList already carrying the store's filters", async () => {
    render(
      <UsersGrid
        hasActiveFilter={false}
        setHasData={vi.fn()}
        setIsLoading={vi.fn()}
        setSearchState={vi.fn()}
        cellHeight="Short"
      />,
    );

    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    const [url, config] = getMock.mock.calls[0];
    expect(url).toBe("/projects/users/");
    expect(config.params.project_id).toBe("proj-1");
    // The request goes out with the filters from the store, not an empty scan.
    expect(config.params.filters).toBe(JSON.stringify(validated));
  });
});
