import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "src/utils/test-utils";
import { useErrorFeedStore } from "../../store";
import ErrorFeedTable from "../ErrorFeedTable";

// While the next page, sort, search or filter loads, the table keeps the
// previous rows on screen (W10-ERROR-FEED-U08), and the view has already
// dropped the selection for the old query (OBS-SW-70). A kept row ticked now
// would stay selected, off screen, once the new rows land, so the kept rows
// must not be selectable. The list query is a stub.
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

// Page-1 rows. With isPlaceholderData they are what React Query keeps on
// screen while another query loads.
const listState = (isPlaceholderData) => ({
  data: { data: [issue("E-1"), issue("E-2")], total: 60 },
  isPending: false,
  isLoading: false,
  isFetching: isPlaceholderData,
  isPaused: false,
  isError: false,
  isPlaceholderData,
  error: null,
  refetch: vi.fn(),
});

const renderTable = (onSelect = vi.fn()) => {
  const { container } = render(
    <ErrorFeedTable selected={[]} onSelect={onSelect} onSelectAll={vi.fn()} />,
  );
  // Header checkbox first, then one per row.
  return within(container.querySelector("table")).getAllByRole("checkbox");
};

describe("ErrorFeedTable selection while the next query loads (W10-ERROR-FEED-U08 x OBS-SW-70)", () => {
  afterEach(() => {
    act(() => {
      useErrorFeedStore.setState(DEFAULT_LIST_STATE);
    });
  });

  it("keeps the previous rows on screen but not selectable", () => {
    query.current = listState(true);
    const checkboxes = renderTable();

    expect(screen.getByText("Issue E-1")).toBeInTheDocument();
    expect(screen.getByText("Issue E-2")).toBeInTheDocument();
    expect(checkboxes).toHaveLength(3);
    checkboxes.forEach((box) => expect(box).toBeDisabled());
  });

  it("lets the rows be selected once the new rows have loaded", () => {
    query.current = listState(false);
    const onSelect = vi.fn();
    const checkboxes = renderTable(onSelect);

    expect(checkboxes).toHaveLength(3);
    checkboxes.forEach((box) => expect(box).toBeEnabled());

    fireEvent.click(checkboxes[1]);
    expect(onSelect).toHaveBeenCalledWith("E-1", true);
  });
});
