import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";

const { agGridState, prefetchCallLogsMock, useCallLogsMock } = vi.hoisted(
  () => ({
    agGridState: { props: null },
    prefetchCallLogsMock: vi.fn(),
    useCallLogsMock: vi.fn(),
  }),
);

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, _ref) {
      agGridState.props = props;
      return (
        <div data-testid="call-logs-grid">
          {props.rowData?.length === 0 && props.noRowsOverlayComponent?.()}
        </div>
      );
    },
  );
  return { AgGridReact };
});

vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQueryClient: () => ({ prefetchQuery: vi.fn() }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({
  useAgTheme: () => ({ withParams: () => ({}) }),
}));
vi.mock("src/sections/agents/helper", () => ({
  getCallLogsColumnDefs: () => [],
  prefetchCallLogs: (...args) => prefetchCallLogsMock(...args),
  useCallLogs: (...args) => useCallLogsMock(...args),
}));
vi.mock("src/sections/agents/store/agentDetailsStore", () => ({
  useAgentDetailsStore: () => ({ selectedVersion: "version-1" }),
}));
vi.mock("src/sections/agents/store", () => ({
  useShallowToggleAnnotationsStore: (selector) =>
    selector({ showMetricsIds: false, reset: vi.fn() }),
}));
vi.mock("src/sections/test-detail/states", () => ({
  resetState: vi.fn(),
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector({ testDetailDrawerOpen: null }),
}));
vi.mock(
  "src/sections/test-detail/TestDetailDrawer/TestDetailSideDrawer",
  () => ({ default: () => null }),
);
vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/project-detail/CompareDrawer/NoRowsOverlay", () => ({
  default: (content) => content,
}));

import CallLogsGrid from "../CallLogsGrid";

const incompleteData = {
  count: 0,
  count_is_lower_bound: true,
  total_pages: 20,
  current_page: 1,
  results: [],
  config: [],
  has_more: false,
  query_complete: false,
  query_status: "degraded",
  query_error_code: "scan_budget_exceeded",
};

const completeData = {
  count: 16,
  count_is_lower_bound: true,
  total_pages: 7,
  current_page: 1,
  results: [{ id: "trace-a", trace_id: "trace-a", status: "completed" }],
  config: [],
  has_more: true,
  next_cursor: "signed-voice-page-2",
  query_complete: true,
  query_status: "complete",
  query_error_code: null,
};

describe("CallLogsGrid bounded-read state", () => {
  beforeEach(() => {
    agGridState.props = null;
    prefetchCallLogsMock.mockReset();
    useCallLogsMock.mockReset();
  });

  it("labels an incomplete page and disables misleading pagination/prefetch", async () => {
    useCallLogsMock.mockReturnValue({
      data: incompleteData,
      isLoading: false,
      error: null,
      queryKey: ["callLogs", "project", "project-1", 15, {}, 1],
    });

    render(<CallLogsGrid id="project-1" module="project" hideDrawer />);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Some results could not be loaded. Please try again.",
    );
    expect(screen.queryByText("No calls found")).not.toBeInTheDocument();
    expect(agGridState.props.rowData).toEqual([]);
    expect(
      screen.queryByRole("button", { name: /go to page 2/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Next").closest("button")).toBeDisabled();
    await waitFor(() => expect(prefetchCallLogsMock).not.toHaveBeenCalled());
  });

  it("keeps complete-page rendering and next-page prefetch unchanged", async () => {
    useCallLogsMock.mockReturnValue({
      data: completeData,
      isLoading: false,
      error: null,
      queryKey: ["callLogs", "project", "project-1", 15, {}, 1],
    });

    render(<CallLogsGrid id="project-1" module="project" hideDrawer />);

    await waitFor(() => expect(prefetchCallLogsMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(agGridState.props.rowData).toEqual(completeData.results);
    expect(agGridState.props.loading).toBe(false);
    expect(
      screen.getByRole("button", { name: /go to page 2/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /go to page 3/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled();
    expect(prefetchCallLogsMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        module: "project",
        id: "project-1",
        page: 2,
        pageLimit: 15,
        paginationParams: {
          cursor_mode: true,
          cursor: "signed-voice-page-2",
          page_size: 15,
        },
      }),
    );
    expect(useCallLogsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        paginationParams: {
          cursor_mode: true,
          page: 1,
          page_size: 15,
        },
      }),
    );
  });

  it("does not request a cursor for a terminal overflow page already buffered locally", async () => {
    useCallLogsMock.mockReturnValue({
      data: {
        ...completeData,
        has_more: false,
        next_cursor: null,
        __exactPage: {
          pending: false,
          stale: false,
          isLastPage: false,
          canPrefetch: false,
        },
      },
      isLoading: false,
      error: null,
      queryKey: ["callLogs", "project", "project-1", 15, {}, 1],
    });

    render(<CallLogsGrid id="project-1" module="project" hideDrawer />);

    expect(
      await screen.findByRole("button", { name: /go to page 2/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled();
    await waitFor(() => expect(prefetchCallLogsMock).not.toHaveBeenCalled());
  });

  it("pauses a bounded same-page continuation until the user explicitly resumes it", async () => {
    useCallLogsMock.mockImplementation(({ paginationRevision }) => ({
      data: paginationRevision === 0 ? undefined : completeData,
      isLoading: false,
      error:
        paginationRevision === 0
          ? { code: "LIST_CURSOR_CONTINUATION_LIMIT" }
          : null,
      queryKey: [
        "callLogs",
        "project",
        "project-1",
        15,
        {},
        1,
        paginationRevision,
      ],
    }));

    render(<CallLogsGrid id="project-1" module="project" hideDrawer />);

    const continueSearch = await screen.findByRole("button", {
      name: "Continue search",
    });
    expect(useCallLogsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ paginationRevision: 0 }),
    );
    expect(screen.queryByText("No calls found")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Some results could not be loaded. Please try again."),
    ).not.toBeInTheDocument();

    await userEvent.click(continueSearch);
    await waitFor(() =>
      expect(useCallLogsMock).toHaveBeenCalledWith(
        expect.objectContaining({ paginationRevision: 1 }),
      ),
    );
    expect(agGridState.props.rowData).toEqual(completeData.results);
    await waitFor(() => expect(prefetchCallLogsMock).toHaveBeenCalledOnce());
  });

  it("keeps buffered rows visible and surfaces retry for any same-generation transport error", async () => {
    const provenRows = [
      { id: "call-proven", trace_id: "call-proven", status: "completed" },
    ];
    const transportError = new Error("network unavailable");
    let seeded = false;
    useCallLogsMock.mockImplementation(
      ({ cursorPagination, paginationRevision }) => {
        if (!seeded) {
          cursorPagination.recordVisibleContinuation(
            0,
            { has_more: true, next_cursor: "saved-checkpoint" },
            { rows: provenRows, response: { data: {} } },
          );
          seeded = true;
        }
        return {
          data: undefined,
          isLoading: false,
          error: transportError,
          queryKey: [
            "callLogs",
            "project",
            "project-1",
            15,
            {},
            1,
            paginationRevision,
          ],
        };
      },
    );

    const view = render(
      <CallLogsGrid id="project-1" module="project" hideDrawer />,
    );
    // The mock records the buffer during the first hook invocation; render
    // once more just as React Query would after publishing the failed state.
    view.rerender(<CallLogsGrid id="project-1" module="project" hideDrawer />);

    expect(agGridState.props.rowData).toEqual(provenRows);
    expect(screen.queryByText("No calls found")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Some results could not be loaded. Please try again."),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Continue search" }),
    ).toBeInTheDocument();
  });

  it("keeps proven rows and cursor navigation usable despite degraded total metadata", async () => {
    useCallLogsMock.mockReturnValue({
      data: {
        ...completeData,
        query_complete: false,
        query_status: "degraded",
        query_error_code: "count_budget_exceeded",
      },
      isLoading: false,
      error: null,
      queryKey: ["callLogs", "project", "project-1", 15, {}, 1],
    });

    render(<CallLogsGrid id="project-1" module="project" hideDrawer />);

    await waitFor(() => expect(prefetchCallLogsMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(agGridState.props.rowData).toEqual(completeData.results);
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled();
  });

  it("disables AG Grid's stale loading announcement for an exact empty result", () => {
    useCallLogsMock.mockReturnValue({
      data: {
        ...completeData,
        count: 0,
        count_is_lower_bound: false,
        total_pages: 1,
        results: [],
        has_more: false,
        next_cursor: null,
      },
      isLoading: false,
      error: null,
      queryKey: ["callLogs", "project", "project-1", 15, {}, 1],
    });

    render(<CallLogsGrid id="project-1" module="project" hideDrawer />);

    expect(screen.getByText("No calls found")).toBeInTheDocument();
    expect(agGridState.props.rowData).toEqual([]);
    expect(agGridState.props.loading).toBe(false);
  });

  it("uses skeleton rows without enabling AG Grid's built-in loading overlay", () => {
    useCallLogsMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
      queryKey: ["callLogs", "project", "project-1", 15, {}, 1],
    });

    render(<CallLogsGrid id="project-1" module="project" hideDrawer />);

    expect(agGridState.props.rowData).toHaveLength(10);
    expect(agGridState.props.loading).toBe(false);
  });
});
