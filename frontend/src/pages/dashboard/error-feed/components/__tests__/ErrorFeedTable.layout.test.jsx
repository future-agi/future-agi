import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "src/utils/test-utils";
import { useErrorFeedStore } from "../../store";
import ErrorFeedTable from "../ErrorFeedTable";

// At a 1024 px window the Error Feed list column is 792 px wide (200 px
// sidebar, 16 px gutters each side). The table's minimum widths have to fit
// in it, or the header runs past the edge, cut at "Trend (1" (L01). The list
// query is a stub that returns an empty list.
const query = vi.hoisted(() => ({ current: {} }));

vi.mock("src/api/errorFeed/error-feed", async (importOriginal) => ({
  ...(await importOriginal()),
  useErrorFeedList: () => query.current,
}));
// Same stub as the sibling ErrorFeedTable tests: the sparkline draws with
// ApexCharts, which has nothing to draw on in jsdom.
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

const loadedEmpty = () => ({
  data: { data: [], total: 0 },
  isPending: false,
  isLoading: false,
  isFetching: false,
  isPaused: false,
  isError: false,
  isPlaceholderData: false,
  error: null,
  refetch: vi.fn(),
});

const renderTable = () =>
  render(
    <ErrorFeedTable selected={[]} onSelect={vi.fn()} onSelectAll={vi.fn()} />,
  );

describe("ErrorFeedTable layout at a 1024 px window (L01)", () => {
  afterEach(() => {
    act(() => {
      useErrorFeedStore.setState(DEFAULT_LIST_STATE);
    });
  });

  it("keeps the table's minimum width inside the 792 px list column", () => {
    query.current = loadedEmpty();

    renderTable();

    const table = screen.getByRole("table");
    expect(table).toHaveStyle({ minWidth: "760px" });
    expect(table).not.toHaveStyle({ minWidth: "900px" });
  });

  it("gives the Error column a floor that leaves the whole header in view", () => {
    query.current = loadedEmpty();

    renderTable();

    const errorHeader = screen.getByRole("columnheader", { name: "Error" });
    expect(errorHeader).toHaveStyle({ minWidth: "180px" });
    expect(errorHeader).not.toHaveStyle({ minWidth: "340px" });
    // The trend and last-seen headers are still rendered, not dropped.
    expect(
      screen.getByRole("columnheader", { name: "Trend (14d)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: /Last seen/ }),
    ).toBeInTheDocument();
  });

  it("keeps the horizontal scroll container for rows that need more room", () => {
    query.current = loadedEmpty();

    renderTable();

    expect(screen.getByRole("table").parentElement).toHaveStyle({
      overflowX: "auto",
    });
  });
});
