import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  userEvent,
} from "src/utils/test-utils";
import { useErrorFeedStore } from "../../store";
import ErrorFeedTable from "../ErrorFeedTable";

// W10-ERROR-FEED-U10: the list's core actions (open an issue, sort a column)
// must work from the keyboard. The list query is a stub holding one issue.
const query = vi.hoisted(() => ({ current: {} }));

vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedList: () => query.current,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
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

const issue = {
  cluster_id: "E-1",
  error: { name: "Tool call timeout", type: "Tool Selection Error" },
  source: "scanner",
  severity: "high",
  status: "escalating",
  trace_count: 7,
  users_affected: 3,
  fix_layer: "prompt",
  trends: [],
  last_seen: "2026-11-01T00:00:00Z",
};

const loaded = () => ({
  data: { data: [issue], total: 1 },
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

describe("ErrorFeedTable keyboard access (W10-ERROR-FEED-U10)", () => {
  afterEach(() => {
    act(() => {
      useErrorFeedStore.setState(DEFAULT_LIST_STATE);
    });
    window.history.pushState({}, "", "/");
  });

  it("opens an issue from a link on its name, in one navigation", () => {
    query.current = loaded();
    renderTable();

    const link = screen.getByRole("link", { name: "Tool call timeout" });
    expect(link).toHaveAttribute("href", "/dashboard/error-feed/E-1");
    // The hover-only arrow stays out of the tab order, so keyboard focus
    // never lands on a button nobody can see.
    expect(
      screen.getByRole("button", { name: "View details" }),
    ).toHaveAttribute("tabindex", "-1");

    const entries = window.history.length;
    fireEvent.click(link);
    expect(window.location.pathname).toBe("/dashboard/error-feed/E-1");
    // The row's own click handler must not push the same page a second time.
    expect(window.history.length).toBe(entries + 1);
  });

  it("sorts from the keyboard and tells assistive tech which column is sorted", async () => {
    const user = userEvent.setup();
    query.current = loaded();
    renderTable();

    const lastSeen = screen.getByRole("button", { name: "Last seen" });
    const severity = screen.getByRole("button", { name: "Severity" });
    expect(lastSeen.closest("th")).toHaveAttribute("aria-sort", "descending");
    expect(severity.closest("th")).not.toHaveAttribute("aria-sort");

    act(() => severity.focus());
    await user.keyboard("{Enter}");

    expect(useErrorFeedStore.getState().sortBy).toBe("severity");
    expect(severity.closest("th")).toHaveAttribute("aria-sort", "descending");
    expect(lastSeen.closest("th")).not.toHaveAttribute("aria-sort");
  });
});
