import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "src/utils/test-utils";
import { useErrorFeedStore } from "../../store";
import ErrorFeedTable from "../ErrorFeedTable";

// Offline, React Query pauses the request for a new search, filter, sort or
// page and keeps the previous query's rows on screen (keepPreviousData). The
// table must say the list is waiting for a connection and that the rows are
// from before the change (OBS-SW-175). The list query is a stub.
const query = vi.hoisted(() => ({ current: {} }));

vi.mock("src/api/errorFeed/error-feed", async (importOriginal) => ({
  ...(await importOriginal()),
  useErrorFeedList: () => query.current,
}));
vi.mock("../ErrorTrendSparkline", () => ({ default: () => null }));

const DEFAULT_LIST_STATE = {
  searchQuery: "",
  selectedProject: "",
  selectedEnvironment: "",
  selectedStatus: "",
  selectedSeverity: "",
  selectedErrorType: "",
  selectedFixLayer: "",
  selectedSource: "",
  timeRange: "7",
  sortBy: "lastSeen",
  sortDir: "desc",
  page: 0,
  pageSize: 25,
};

const issue = (clusterId) => ({
  cluster_id: clusterId,
  error: { name: `Issue ${clusterId}`, type: "Tool Selection Error" },
  source: "scanner",
  severity: "high",
  status: "escalating",
  trace_count: 7,
  users_affected: 3,
  fix_layer: "prompt",
  trends: [],
  last_seen: "2026-11-01T00:00:00Z",
});

// The previous query's result, kept on screen while the next one waits.
// isPaused: offline, so the request has not been sent.
const nextQueryWaiting = ({ rows, total, isPaused }) => ({
  data: { data: rows, total },
  isPending: false,
  isLoading: false,
  isFetching: !isPaused,
  isPaused,
  isError: false,
  isPlaceholderData: true,
  error: null,
  refetch: vi.fn(),
});

const renderTable = () =>
  render(
    <ErrorFeedTable selected={[]} onSelect={vi.fn()} onSelectAll={vi.fn()} />,
  );

describe("ErrorFeedTable while the next query waits for a connection (OBS-SW-175)", () => {
  afterEach(() => {
    act(() => {
      useErrorFeedStore.setState(DEFAULT_LIST_STATE);
    });
  });

  it("says the kept rows are from before the change and no page is loading", () => {
    query.current = nextQueryWaiting({
      rows: [issue("E-1"), issue("E-2")],
      total: 60,
      isPaused: true,
    });

    renderTable();

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/you're offline/i);
    expect(status).toHaveTextContent(/from before your last change/i);
    expect(status).toHaveTextContent(/update when you reconnect/i);
    expect(screen.queryByText(/loading page/i)).not.toBeInTheDocument();
    // The previous rows stay on screen behind the notice.
    expect(screen.getByText("Issue E-1")).toBeInTheDocument();
    expect(screen.getByText("Issue E-2")).toBeInTheDocument();
  });

  it("says results will load on reconnect when there are no rows to keep", () => {
    query.current = nextQueryWaiting({ rows: [], total: 0, isPaused: true });

    renderTable();

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/you're offline/i);
    expect(status).toHaveTextContent(/results will load when you reconnect/i);
    expect(status).not.toHaveTextContent(/before your last change/i);
  });

  it("keeps the plain loading status while the next query loads online", () => {
    query.current = nextQueryWaiting({
      rows: [issue("E-1"), issue("E-2")],
      total: 60,
      isPaused: false,
    });

    renderTable();

    expect(screen.getByRole("status")).toHaveTextContent("Loading page");
    expect(screen.queryByText(/offline/i)).not.toBeInTheDocument();
    expect(screen.getByText("Issue E-1")).toBeInTheDocument();
  });
});
