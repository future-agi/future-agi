import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import { useErrorFeedStore } from "../../store";
import ErrorFeedTable from "../ErrorFeedTable";

// The list query is a stub so each case can put the feed in one state:
// failed, paused while offline, or loaded with no rows.
const query = vi.hoisted(() => ({ current: {} }));

vi.mock("src/api/errorFeed/error-feed", () => ({
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

const failed = (refetch) => ({
  data: undefined,
  isPending: false,
  isLoading: false,
  isFetching: false,
  isPaused: false,
  isError: true,
  error: new Error("Network Error"),
  refetch,
});

const pausedOffline = (refetch) => ({
  data: undefined,
  isPending: true,
  isLoading: false,
  isFetching: false,
  isPaused: true,
  isError: false,
  error: null,
  refetch,
});

const loadedEmpty = (refetch) => ({
  data: { data: [], total: 0 },
  isPending: false,
  isLoading: false,
  isFetching: false,
  isPaused: false,
  isError: false,
  error: null,
  refetch,
});

const renderTable = () =>
  render(
    <ErrorFeedTable selected={[]} onSelect={vi.fn()} onSelectAll={vi.fn()} />,
  );

describe("ErrorFeedTable load and empty states (W10-ERROR-FEED-U01)", () => {
  afterEach(() => {
    act(() => {
      useErrorFeedStore.setState(DEFAULT_LIST_STATE);
    });
  });

  it("says the feed failed to load and offers Retry instead of the all-clear", () => {
    const refetch = vi.fn();
    query.current = failed(refetch);

    renderTable();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load the Error Feed",
    );
    expect(
      screen.queryByText(/everything looks good/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/no errors/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("shows the load failure while the request waits for a connection", () => {
    query.current = pausedOffline(vi.fn());

    renderTable();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load the Error Feed",
    );
    expect(
      screen.queryByText(/everything looks good/i),
    ).not.toBeInTheDocument();
  });

  it("treats a Fix layer filter as a filter and clears it", () => {
    useErrorFeedStore.setState({ selectedFixLayer: "Guardrails" });
    query.current = loadedEmpty(vi.fn());

    renderTable();

    expect(
      screen.getByText("No errors match your filters"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/everything looks good/i),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(useErrorFeedStore.getState().selectedFixLayer).toBe("");
    expect(
      screen.getByText("No errors in the last 7 days"),
    ).toBeInTheDocument();
  });

  it("treats a Source filter as a filter", () => {
    useErrorFeedStore.setState({ selectedSource: "eval" });
    query.current = loadedEmpty(vi.fn());

    renderTable();

    expect(
      screen.getByText("No errors match your filters"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Clear filters" }),
    ).toBeInTheDocument();
  });

  it("names the time window and the next step when nothing is filtered", () => {
    query.current = loadedEmpty(vi.fn());

    renderTable();

    expect(
      screen.getByText("No errors in the last 7 days"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Errors appear once a tracing project sends traces and the scanner or an eval task runs.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open tracing/i })).toHaveAttribute(
      "href",
      "/dashboard/observe",
    );
    expect(
      screen.getByRole("link", { name: /read the docs/i }),
    ).toHaveAttribute("href", "https://docs.futureagi.com/docs/error-feed");
    expect(
      screen.queryByText(/everything looks good/i),
    ).not.toBeInTheDocument();
  });

  it("reads a one-day window as the last 24 hours", () => {
    useErrorFeedStore.setState({ timeRange: "1" });
    query.current = loadedEmpty(vi.fn());

    renderTable();

    expect(
      screen.getByText("No errors in the last 24 hours"),
    ).toBeInTheDocument();
  });
});
