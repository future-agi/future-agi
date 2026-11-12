/* eslint-disable react/prop-types */
// A voice project's call list footer says which calls are on screen ("1–25"),
// and adds "of N" only when the list API reported an exact total: the voice
// list's `count` is a lower bound while more calls may follow
// (count_is_lower_bound), so no total is printed then. The footer is the
// shared CursorGridPagination; this file checks what the call list passes it.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

const {
  queryClientMock,
  simulationAgentsState,
  useCallLogsMock,
  useQueryMock,
} = vi.hoisted(() => ({
  queryClientMock: { prefetchQuery: vi.fn() },
  simulationAgentsState: { data: [] },
  useCallLogsMock: vi.fn(),
  useQueryMock: vi.fn(),
}));

vi.mock("src/hooks/use-fit-height", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => 612,
}));
vi.mock("ag-grid-react", async (importOriginal) => {
  const { createElement, forwardRef } = await import("react");
  return {
    ...(await importOriginal()),
    AgGridReact: forwardRef(function AgGridReactDouble(_props, _ref) {
      return createElement("div", { "data-testid": "call-logs-grid" });
    }),
  };
});
vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: (...args) => useQueryMock(...args),
  useQueryClient: () => queryClientMock,
}));
vi.mock("src/hooks/use-ag-theme", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgThemeWith: () => ({}),
}));
vi.mock("src/sections/agents/helper", async (importOriginal) => ({
  ...(await importOriginal()),
  getCallLogsColumnDefs: () => [],
  prefetchCallLogs: vi.fn(),
  useCallLogs: (...args) => useCallLogsMock(...args),
}));
vi.mock(
  "src/sections/agents/store/agentDetailsStore",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useAgentDetailsStore: () => ({ selectedVersion: "version-1" }),
  }),
);
vi.mock("src/sections/agents/store", async (importOriginal) => ({
  ...(await importOriginal()),
  useShallowToggleAnnotationsStore: (selector) =>
    selector({ showMetricsIds: false, reset: vi.fn() }),
}));
vi.mock("src/sections/test-detail/states", async (importOriginal) => ({
  ...(await importOriginal()),
  resetState: vi.fn(),
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector({ testDetailDrawerOpen: null }),
}));
// The call drawer module has only a default export and loads the whole voice
// drawer; like the sibling CallLogsGrid suites, stub it outright.
vi.mock(
  "src/sections/test-detail/TestDetailDrawer/TestDetailSideDrawer",
  () => ({ default: () => null }),
);
vi.mock("src/components/show", async (importOriginal) => ({
  ...(await importOriginal()),
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useSimulationAgents: () => simulationAgentsState,
}));

import CallLogsGrid from "../CallLogsGrid";

const calls = (n) =>
  Array.from({ length: n }, (_, index) => ({ id: `call-${index + 1}` }));

const projectRead = (overrides = {}) => ({
  data: {
    count: 0,
    count_is_lower_bound: false,
    total_pages: 1,
    current_page: 1,
    results: [],
    config: [],
    has_more: false,
    next_cursor: null,
    query_complete: true,
    query_status: "complete",
    query_error_code: null,
    ...overrides,
  },
  isLoading: false,
  error: null,
  queryKey: ["callLogs", "project", "project-1", 25, {}, 1, 0],
});

const renderList = () =>
  render(
    <CallLogsGrid id="project-1" module="project" fitToWindow hideDrawer />,
  );

describe("CallLogsGrid footer range", () => {
  beforeEach(() => {
    useCallLogsMock.mockReset();
    useQueryMock.mockReset().mockReturnValue({ data: undefined });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the calls on screen and the exact total when the whole list is known", () => {
    useCallLogsMock.mockReturnValue(
      projectRead({ count: 3, count_is_lower_bound: false, results: calls(3) }),
    );

    renderList();

    expect(screen.getByTestId("pager-range")).toHaveTextContent("1–3 of 3");
  });

  it("shows the calls on screen without a total while more calls may follow", () => {
    useCallLogsMock.mockReturnValue(
      projectRead({
        count: 26,
        count_is_lower_bound: true,
        total_pages: 2,
        has_more: true,
        next_cursor: "cursor-page-2",
        results: calls(25),
      }),
    );

    renderList();

    const range = screen.getByTestId("pager-range");
    expect(range).toHaveTextContent("1–25");
    expect(range.textContent).not.toMatch(/of/);
  });

  it("draws no range on an empty call list", () => {
    useCallLogsMock.mockReturnValue(projectRead());

    renderList();

    expect(screen.queryByTestId("pager-range")).not.toBeInTheDocument();
    expect(screen.getByText("Results per page")).toBeInTheDocument();
  });

  it("draws no range while the page is loading", () => {
    useCallLogsMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
      queryKey: ["callLogs", "project", "project-1", 25, {}, 1, 0],
    });

    renderList();

    expect(screen.queryByTestId("pager-range")).not.toBeInTheDocument();
  });
});
