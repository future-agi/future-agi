import React from "react";
import { createGrid, ModuleRegistry } from "ag-grid-community";
import { AllEnterpriseModule } from "ag-grid-enterprise";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, waitFor } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";

// AG Grid's own pager prints the row count the Users datasource hands it as
// "of N". These tests run that datasource in a real server-side grid set up
// with the paging options the Users grid passes, and read the pager's text.

const storedValues = new Map();
vi.stubGlobal("localStorage", {
  clear: () => storedValues.clear(),
  getItem: (key) => storedValues.get(key) ?? null,
  removeItem: (key) => storedValues.delete(key),
  setItem: (key, value) => storedValues.set(key, String(value)),
});

const { getMock, gridState } = vi.hoisted(() => ({
  getMock: vi.fn(),
  gridState: { props: null, api: null },
}));

vi.mock("ag-grid-react", async (importOriginal) => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function CapturedAgGridReact(props, ref) {
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
      // eslint-disable-next-line react/prop-types
      return <div>{props.noRowsOverlayComponent?.()}</div>;
    },
  );
  return { ...(await importOriginal()), AgGridReact };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("src/hooks/use-ag-theme", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgThemeWith: () => ({}),
}));
vi.mock("src/hooks/use-debounce", async (importOriginal) => ({
  ...(await importOriginal()),
  useDebounce: (value) => value,
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
  useParams: () => ({ observeId: "project-1" }),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  readQuery: (...args) => getMock(...args),
}));

import UsersGrid from "../UsersGrid";
import useUsersStore from "../Store/usersStore";

ModuleRegistry.registerModules([AllEnterpriseModule]);

// The captured grid draws the Users empty state, which uses these glyphs.
for (const name of ["solar:inbox-linear", "solar:arrow-right-up-linear"]) {
  registerOfflineIcon(name, bundled[name]);
}

const DATE_WINDOW = [
  {
    column_id: "created_at",
    filter_config: {
      filter_type: "datetime",
      filter_op: "between",
      filter_value: ["2026-03-01T00:00:00.000Z", "2026-06-01T00:00:00.000Z"],
    },
  },
];

const users = (from, to) =>
  Array.from({ length: to - from }, (_, index) => ({
    user_id: `user-${from + index}`,
    end_user_id: `end-user-${from + index}`,
  }));

// One page from the Users cursor API. A page with a next cursor has more
// users after it, so its total_count is only a lower bound.
const cursorPage = ({ rows, totalCount, nextCursor = null }) => ({
  data: {
    result: {
      table: rows,
      total_count: totalCount,
      count_is_lower_bound: nextCursor !== null,
      has_more: nextCursor !== null,
      next_cursor: nextCursor,
      query_complete: true,
      query_status: "complete",
    },
  },
});

// One page from the numbered Users API, which has no cursor fields and
// reports an exact total.
const numberedPage = ({ rows, totalCount }) => ({
  data: {
    result: {
      table: rows,
      total_count: totalCount,
      count_is_lower_bound: false,
      query_complete: true,
      query_status: "complete",
    },
  },
});

const mountedGrids = [];

// Renders the Users grid, then runs its datasource in a real server-side
// AG Grid with the paging options the Users grid passes to its own grid.
const mountUsersGrid = () => {
  render(
    <UsersGrid
      hasActiveFilter={false}
      setHasData={vi.fn()}
      setIsLoading={vi.fn()}
      setSearchState={vi.fn()}
      cellHeight="Short"
    />,
  );
  const options = gridState.props;
  const host = document.createElement("div");
  document.body.appendChild(host);
  let api;
  act(() => {
    api = createGrid(host, {
      theme: "legacy",
      domLayout: "autoHeight",
      columnDefs: [{ field: "user_id" }],
      rowModelType: options.rowModelType,
      pagination: options.pagination,
      paginationPageSize: options.paginationPageSize,
      paginationPageSizeSelector: options.paginationPageSizeSelector,
      cacheBlockSize: options.cacheBlockSize,
      maxBlocksInCache: options.maxBlocksInCache,
      maxConcurrentDatasourceRequests: options.maxConcurrentDatasourceRequests,
      serverSideInitialRowCount: options.serverSideInitialRowCount,
      suppressServerSideFullWidthLoadingRow:
        options.suppressServerSideFullWidthLoadingRow,
      getRowId: options.getRowId,
      getLocaleText: options.getLocaleText,
      serverSideDatasource: options.serverSideDatasource,
    });
    gridState.api = api;
  });
  mountedGrids.push({ api, host });
  return host;
};

// AG Grid draws each word and number of its pager in its own span.
const pagerText = (host, selector) => {
  const panel = host.querySelector(selector);
  if (!panel) return null;
  return Array.from(panel.children, (part) => part.textContent.trim())
    .filter(Boolean)
    .join(" ");
};
const rowSummary = (host) => pagerText(host, ".ag-paging-row-summary-panel");
const pageSummary = (host) => pagerText(host, ".ag-paging-description");
const pagerButton = (host, label) =>
  host.querySelector(`.ag-paging-button[aria-label="${label}"]`);

describe("Users list pager total", () => {
  beforeEach(() => {
    getMock.mockReset();
    gridState.props = null;
    gridState.api = null;
    localStorage.clear();
    useUsersStore.setState({
      searchQuery: "",
      filters: DATE_WINDOW,
      columns: [],
      gridApi: null,
      selectedAll: false,
      selectedRowsData: [],
      sortParams: [],
    });
  });

  afterEach(() => {
    while (mountedGrids.length > 0) {
      const { api, host } = mountedGrids.pop();
      act(() => api.destroy());
      host.remove();
    }
  });

  it("says more instead of a total while the users API reports only a lower bound", async () => {
    getMock.mockResolvedValueOnce(
      cursorPage({
        rows: users(0, 25),
        totalCount: 26,
        nextCursor: "after-user-25",
      }),
    );
    const host = mountUsersGrid();

    await waitFor(() => expect(rowSummary(host)).toBe("1 to 25 of more"));
    expect(pageSummary(host)).toBe("Page 1 of more");
    expect(host.querySelector(".ag-paging-panel")).not.toHaveTextContent("26");
    expect(pagerButton(host, "Next Page")).toHaveAttribute(
      "aria-disabled",
      "false",
    );
    expect(getMock).toHaveBeenCalledTimes(1);
  });

  it("prints the exact total once the last users page arrives, and keeps it on the way back", async () => {
    getMock
      .mockResolvedValueOnce(
        cursorPage({
          rows: users(0, 25),
          totalCount: 26,
          nextCursor: "after-user-25",
        }),
      )
      .mockResolvedValueOnce(
        cursorPage({ rows: users(25, 30), totalCount: 30 }),
      );
    const host = mountUsersGrid();
    await waitFor(() => expect(rowSummary(host)).toBe("1 to 25 of more"));

    // The pager's buttons take Enter as well as a click.
    fireEvent.keyDown(pagerButton(host, "Next Page"), { key: "Enter" });

    await waitFor(() => expect(rowSummary(host)).toBe("26 to 30 of 30"));
    expect(pageSummary(host)).toBe("Page 2 of 2");
    expect(pagerButton(host, "Next Page")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(getMock.mock.calls[1][1].params).toEqual(
      expect.objectContaining({ cursor_mode: true, cursor: "after-user-25" }),
    );

    fireEvent.click(pagerButton(host, "Previous Page"));

    await waitFor(() => expect(rowSummary(host)).toBe("1 to 25 of 30"));
    expect(pageSummary(host)).toBe("Page 1 of 2");
    expect(getMock).toHaveBeenCalledTimes(2);
  });

  it("prints the exact total of a users list that fits on one page", async () => {
    getMock.mockResolvedValueOnce(
      cursorPage({ rows: users(0, 3), totalCount: 3 }),
    );
    const host = mountUsersGrid();

    await waitFor(() => expect(rowSummary(host)).toBe("1 to 3 of 3"));
    expect(pageSummary(host)).toBe("Page 1 of 1");
  });

  it("prints an exact total the users API reports before the last page", async () => {
    getMock.mockResolvedValueOnce(
      numberedPage({ rows: users(0, 25), totalCount: 60 }),
    );
    const host = mountUsersGrid();

    await waitFor(() => expect(rowSummary(host)).toBe("1 to 25 of 60"));
    expect(pageSummary(host)).toBe("Page 1 of 3");
    expect(pagerButton(host, "Next Page")).toHaveAttribute(
      "aria-disabled",
      "false",
    );
  });
});
