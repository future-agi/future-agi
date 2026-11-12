import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "src/utils/test-utils";
import ErrorFeedView from "../ErrorFeedView";
import { useErrorFeedStore } from "../store";

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
  activeTab: "overview",
};

// 60 issues; each page's ids carry the page offset ("O0-*" on page 1,
// "O25-*" on page 2) so a stale selection is easy to tell apart.
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedList: (params) => ({
    data: {
      data: [0, 1, 2].map((i) => ({
        cluster_id: `O${params.offset}-${i}`,
        error: {
          name: `Issue O${params.offset}-${i}`,
          type: "ToolSelectionError",
        },
        source: "scanner",
        severity: "high",
        status: "escalating",
        trace_count: 3,
        users_affected: 1,
        fix_layer: "prompt",
        trends: [],
        lastSeenHuman: "1h ago",
      })),
      total: 60,
    },
    isLoading: false,
  }),
}));

// The filter bar is where bulk actions read the selection: the stub shows
// exactly the ids a bulk action would change.
vi.mock("../components/ErrorFeedFilters", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ selected }) => (
    <div data-testid="selected-issues">{selected.join(",")}</div>
  ),
}));
vi.mock("src/components/page-header", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../components/ErrorTrendSparkline", () => ({ default: () => null }));
vi.mock("../components/ErrorStatusChip", () => ({ default: () => null }));
vi.mock("../components/ErrorSeverityBadge", () => ({ default: () => null }));

const selectedIssues = () => screen.getByTestId("selected-issues").textContent;

const headerCheckbox = (container) =>
  within(container.querySelector("thead")).getByRole("checkbox");

const rowCheckboxes = (container) =>
  within(container.querySelector("tbody")).getAllByRole("checkbox");

describe("Error Feed list selection follows the visible page", () => {
  afterEach(() => {
    act(() => {
      useErrorFeedStore.setState(DEFAULT_LIST_STATE);
    });
  });

  it("clears a page-1 select-all when the user moves to page 2", () => {
    const { container } = render(<ErrorFeedView />);

    fireEvent.click(headerCheckbox(container));
    expect(selectedIssues()).toBe("O0-0,O0-1,O0-2");

    act(() => {
      useErrorFeedStore.getState().setPage(1);
    });

    expect(screen.getByText("Issue O25-0")).toBeInTheDocument();
    expect(selectedIssues()).toBe("");
    expect(headerCheckbox(container)).not.toBeChecked();
    expect(headerCheckbox(container)).toHaveAttribute(
      "data-indeterminate",
      "false",
    );
  });

  it.each([
    ["status filter", (store) => store.setSelectedStatus("resolved")],
    ["search", (store) => store.setSearchQuery("timeout")],
    ["sort", (store) => store.setSortBy("severity")],
    ["page size", () => useErrorFeedStore.setState({ pageSize: 50, page: 0 })],
  ])("clears the selection when the %s changes", (_label, change) => {
    const { container } = render(<ErrorFeedView />);

    fireEvent.click(rowCheckboxes(container)[0]);
    expect(selectedIssues()).toBe("O0-0");

    act(() => {
      change(useErrorFeedStore.getState());
    });

    expect(selectedIssues()).toBe("");
  });

  it("keeps the selection while the list query stays the same", () => {
    const { container } = render(<ErrorFeedView />);

    fireEvent.click(rowCheckboxes(container)[0]);
    fireEvent.click(rowCheckboxes(container)[2]);

    act(() => {
      useErrorFeedStore.getState().setActiveTab("traces");
    });

    expect(selectedIssues()).toBe("O0-0,O0-2");
    expect(headerCheckbox(container)).toHaveAttribute(
      "data-indeterminate",
      "true",
    );
  });
});
