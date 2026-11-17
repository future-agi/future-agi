import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import ObserveHeader from "../ObserveHeader";
import {
  OBSERVE_LIST_REFRESH_EVENT,
  OBSERVE_PAGE_CHANGED_EVENT,
} from "../observeEvents";

const h = vi.hoisted(() => ({
  invalidateQueries: vi.fn(),
  useMutation: vi.fn(),
  post: vi.fn(),
  get: vi.fn(),
  getStorage: vi.fn(() => false),
  setStorage: vi.fn(),
  observeId: "project-1",
  pathname: "/dashboard/observe/project-1/llm-tracing",
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: h.useMutation,
  useQueryClient: () => ({ invalidateQueries: h.invalidateQueries }),
}));

vi.mock("react-router", () => ({
  useNavigate: () => vi.fn(),
  useParams: () => ({ observeId: h.observeId }),
  useLocation: () => ({ pathname: h.pathname }),
}));

vi.mock("src/hooks/use-local-storage", () => ({
  getStorage: h.getStorage,
  setStorage: h.setStorage,
}));
vi.mock("src/routes/hooks/use-url-state", () => ({
  useUrlState: (_key, initial) => [initial, vi.fn()],
}));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: null }),
}));
vi.mock("../LLMTracing/common", () => ({
  useProjectList: () => ({ data: [], isLoading: false }),
  DOC_LINKS: {
    llmTracing: "https://example.test/tracing",
    sessions: "https://example.test/sessions",
    users: "https://example.test/users",
  },
}));
vi.mock("../LLMTracing/states", () => ({
  resetTraceGridStore: vi.fn(),
  resetSpanGridStore: vi.fn(),
}));
// Keep the real endpoint registry; only the HTTP transport is replaced.
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: h.get, post: h.post },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid={icon} />,
}));
vi.mock("src/components/tooltip/CustomTooltip", () => {
  const MockTooltip = ({ children }) => children;
  MockTooltip.propTypes = { children: PropTypes.node };
  return { default: MockTooltip };
});
vi.mock("../SharedComponents", () => {
  const MockObserveIconButton = ({ children, ...props }) => (
    <button type="button" {...props}>
      {children}
    </button>
  );
  MockObserveIconButton.propTypes = { children: PropTypes.node };
  return { ObserveIconButton: MockObserveIconButton };
});
vi.mock("src/components/share-dialog", () => ({ ShareDialog: () => null }));
vi.mock("src/sections/project/TagEditor", () => ({ default: () => null }));
vi.mock("../../project-detail/ConfigureProject", () => ({
  default: () => null,
}));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));

const renderHeader = (refreshData = vi.fn()) => {
  render(<ObserveHeader text="LLM Tracing" refreshData={refreshData} />);
  return refreshData;
};

describe("ObserveHeader exact aggregation refresh state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // clearAllMocks keeps queued once-responses; a test that stops early
    // would hand its unread status responses to the next test's poll.
    h.get.mockReset();
    h.post.mockReset();
    h.getStorage.mockReturnValue(false);
    h.observeId = "project-1";
    h.pathname = "/dashboard/observe/project-1/llm-tracing";
  });

  afterEach(() => vi.useRealTimers());

  it("passes the grid attribute projection through to export creation", async () => {
    h.post.mockResolvedValue({ data: { id: "job", status: "failed" } });
    render(<ObserveHeader
      text="LLM Tracing" selectedTab="trace" filterTrace={[]}
      traceAttributeKeys={["metadata.path,with-comma"]}
    />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Export all rows" }));
    });
    expect(h.post).toHaveBeenCalledWith("/tracer/bulk-exports/", {
      project_id: "project-1", row_type: "traces", format: "csv",
      filters: { filters: [], attribute_keys: ["metadata.path,with-comma"] },
    });
  });

  it("shows last updated only after an exact completion event", () => {
    renderHeader();
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-completed", {
          detail: {
            observeId: "project-1",
            queryCompletedAt: "2026-08-03T12:34:00",
          },
        }),
      );
    });

    expect(
      screen.getByText(/Last updated on 03\/08\/2026/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/12:34 pm/i)).toBeInTheDocument();
  });

  it("scopes completion events to the current route and resets on navigation", () => {
    const view = render(
      <ObserveHeader text="LLM Tracing" refreshData={vi.fn()} />,
    );

    act(() => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-completed", {
          detail: {
            observeId: "project-2",
            queryCompletedAt: "2026-08-04T12:34:00Z",
          },
        }),
      );
    });
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("observe-aggregation-completed", {
          detail: {
            observeId: "project-1",
            queryCompletedAt: "2026-08-03T12:34:00Z",
          },
        }),
      );
    });
    expect(screen.getByText(/Last updated on/i)).toBeInTheDocument();

    h.observeId = "project-2";
    h.pathname = "/dashboard/observe/project-2/llm-tracing";
    view.rerender(<ObserveHeader text="LLM Tracing" refreshData={vi.fn()} />);
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();
  });

  it("uses explicit reload for aggregation and does not stamp request time", () => {
    const refreshData = renderHeader();
    const aggregationRefresh = vi.fn();
    window.addEventListener("observe-refresh", aggregationRefresh, {
      once: true,
    });

    fireEvent.click(
      screen.getByTestId("solar:refresh-linear").closest("button"),
    );

    expect(refreshData).toHaveBeenCalledWith({ includeAggregations: false });
    expect(aggregationRefresh).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();
  });

  const setAggregationRefreshing = (sourceId, refreshing) => {
    window.dispatchEvent(
      new CustomEvent("observe-aggregation-refresh-state", {
        detail: { observeId: "project-1", sourceId, refreshing },
      }),
    );
  };

  it("keeps grid reload available while exact aggregations refresh", () => {
    // A revisit's background revalidation must not lock the grids' Reload
    // for the whole exact read. While any aggregation refreshes, Reload
    // reloads rows only; the charts keep polling their own refresh.
    const refreshData = renderHeader();
    const aggregationRefresh = vi.fn();
    const listRefresh = vi.fn();
    window.addEventListener("observe-refresh", aggregationRefresh);
    window.addEventListener(OBSERVE_LIST_REFRESH_EVENT, listRefresh);

    act(() => {
      setAggregationRefreshing("primary", true);
      setAggregationRefreshing("compare", true);
    });

    const rowsOnly = screen.getByRole("button", {
      name: "Reload rows (charts are refreshing)",
    });
    expect(rowsOnly).toBeEnabled();
    fireEvent.click(rowsOnly);
    expect(refreshData).toHaveBeenCalledWith({ includeAggregations: false });
    expect(listRefresh).toHaveBeenCalledOnce();
    expect(aggregationRefresh).not.toHaveBeenCalled();

    act(() => setAggregationRefreshing("primary", false));
    expect(
      screen.getByRole("button", {
        name: "Reload rows (charts are refreshing)",
      }),
    ).toBeEnabled();

    act(() => setAggregationRefreshing("compare", false));
    const reload = screen.getByRole("button", { name: "Reload data" });
    expect(reload).toBeEnabled();
    fireEvent.click(reload);
    expect(aggregationRefresh).toHaveBeenCalledOnce();

    window.removeEventListener("observe-refresh", aggregationRefresh);
    window.removeEventListener(OBSERVE_LIST_REFRESH_EVENT, listRefresh);
  });

  it("keeps 10-second auto refresh scoped to list data", () => {
    vi.useFakeTimers();
    h.getStorage.mockReturnValue(true);
    const aggregationRefresh = vi.fn();
    const listRefresh = vi.fn();
    window.addEventListener("observe-refresh", aggregationRefresh, {
      once: true,
    });
    window.addEventListener(OBSERVE_LIST_REFRESH_EVENT, listRefresh, {
      once: true,
    });
    const refreshData = renderHeader();

    act(() => vi.advanceTimersByTime(10_000));

    expect(refreshData).not.toHaveBeenCalled();
    expect(listRefresh).toHaveBeenCalledOnce();
    expect(aggregationRefresh).not.toHaveBeenCalled();
    expect(screen.queryByText(/Last updated on/i)).not.toBeInTheDocument();
  });

  it("turns auto refresh off when an explicit paginator leaves page one", () => {
    vi.useFakeTimers();
    h.getStorage.mockReturnValue(true);
    const listRefresh = vi.fn();
    window.addEventListener(OBSERVE_LIST_REFRESH_EVENT, listRefresh);
    renderHeader();

    act(() => {
      window.dispatchEvent(
        new CustomEvent(OBSERVE_PAGE_CHANGED_EVENT, { detail: { page: 2 } }),
      );
    });
    expect(h.setStorage).toHaveBeenCalledWith("autoRefresh", false);

    act(() => vi.advanceTimersByTime(10_000));
    expect(listRefresh).not.toHaveBeenCalled();
    window.removeEventListener(OBSERVE_LIST_REFRESH_EVENT, listRefresh);
  });

  it("keeps the page-change shutoff armed while auto refresh is off", () => {
    h.getStorage.mockReturnValue(false);
    renderHeader();

    act(() => {
      window.dispatchEvent(
        new CustomEvent(OBSERVE_PAGE_CHANGED_EVENT, { detail: { page: 2 } }),
      );
    });

    expect(h.setStorage).toHaveBeenCalledWith("autoRefresh", false);
  });

  it.each([
    ["LLM Tracing", "traces", "traces", "/dashboard/observe/project-1/llm-tracing"],
    ["LLM Tracing", "spans", "spans", "/dashboard/observe/project-1/llm-tracing"],
    ["Sessions", "sessions", "sessions", "/dashboard/observe/project-1/sessions"],
  ])("exports every filtered %s row through a job (%s)", async (text, selectedTab, rowType, pathname) => {
    vi.useFakeTimers();
    h.pathname = pathname;
    const filters = [{ column_id: "status", value: "OK" }];
    h.post.mockResolvedValue({ data: { id: "job-1", status: "queued" } });
    h.get.mockResolvedValueOnce({ data: { id: "job-1", status: "running" } })
      .mockResolvedValueOnce({ data: { id: "job-1", status: "completed", download_url: "/exports/job-1.csv" } });
    render(<ObserveHeader text={text} selectedTab={selectedTab} filterTrace={filters} filterSpan={filters} filterSession={filters} refreshData={vi.fn()} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Export all rows" }));
    });
    expect(h.post).toHaveBeenCalledWith("/tracer/bulk-exports/", {
      project_id: "project-1", row_type: rowType, format: "csv",
      filters: { filters, ...(rowType === "traces" ? { attribute_keys: [] } : {}) },
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    // The download re-signs on click (BulkExportButton.test covers the fetch),
    // so the completed job offers a button rather than a cached link.
    expect(h.get).toHaveBeenCalledWith("/tracer/bulk-exports/job-1/");
    expect(screen.getByRole("button", { name: "Download export" })).toBeEnabled();
    expect(screen.queryByRole("link", { name: "Download export" })).not.toBeInTheDocument();
  });

  it("shows a failed export without offering a partial download", async () => {
    vi.useFakeTimers();
    h.post.mockResolvedValue({ data: { id: "job-failed", status: "queued" } });
    h.get.mockResolvedValue({ data: { id: "job-failed", status: "failed", row_count: 100 } });
    renderHeader();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Export all rows" }));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByRole("alert")).toHaveTextContent("Export could not be completed");
    expect(screen.queryByRole("button", { name: "Download export" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export all rows" })).toBeEnabled();
  });

  it("retries polling the same job after a status request fails", async () => {
    vi.useFakeTimers();
    h.post.mockResolvedValue({ data: { id: "job-retry", status: "queued" } });
    h.get.mockRejectedValueOnce(new Error("status unavailable"))
      .mockResolvedValueOnce({ data: { id: "job-retry", status: "completed", download_url: "/exports/job-retry.csv" } });
    renderHeader();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Export all rows" }));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    fireEvent.click(screen.getByRole("button", { name: "Retry export status" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.get).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Download export" })).toBeEnabled();
  });

  it("keeps the Users export control hidden", () => {
    h.pathname = "/dashboard/observe/project-1/users";
    render(<ObserveHeader text="Users" refreshData={vi.fn()} />);

    expect(
      screen.queryByRole("button", {
        name: "Export all rows",
      }),
    ).not.toBeInTheDocument();
  });
});
