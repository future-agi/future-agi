import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, within } from "src/utils/test-utils";
import ErrorFeedTable from "../ErrorFeedTable";

// Only the header checkbox is under test; the list query and the heavy cells
// are stubs.
const state = vi.hoisted(() => ({ rows: [] }));

vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedList: () => ({
    data: { data: state.rows, total: 60 },
    isLoading: false,
  }),
}));
vi.mock("../ErrorTrendSparkline", () => ({ default: () => null }));
vi.mock("../ErrorStatusChip", () => ({ default: () => null }));
vi.mock("../ErrorSeverityBadge", () => ({ default: () => null }));

const row = (clusterId) => ({
  cluster_id: clusterId,
  error: { name: `Issue ${clusterId}`, type: "ToolSelectionError" },
  source: "scanner",
  severity: "high",
  status: "escalating",
  trace_count: 3,
  users_affected: 1,
  fix_layer: "prompt",
  trends: [],
  lastSeenHuman: "1h ago",
});

// The rows on screen. "P1-*" ids belong to another page and are not shown.
const VISIBLE = ["P2-1", "P2-2", "P2-3"];

const renderTable = (selected) => {
  state.rows = VISIBLE.map(row);
  const onSelectAll = vi.fn();
  const { container } = render(
    <ErrorFeedTable
      selected={selected}
      onSelect={vi.fn()}
      onSelectAll={onSelectAll}
    />,
  );
  const header = within(container.querySelector("thead")).getByRole("checkbox");
  return { header, onSelectAll };
};

describe("ErrorFeedTable header checkbox follows the visible rows", () => {
  it("is unchecked when only issues from another page are selected", () => {
    const { header } = renderTable(["P1-1", "P1-2", "P1-3"]);

    expect(header).not.toBeChecked();
    expect(header).toHaveAttribute("data-indeterminate", "false");
  });

  it("is indeterminate when only some visible rows are selected", () => {
    const { header } = renderTable(["P1-1", "P1-2", "P2-1"]);

    expect(header).not.toBeChecked();
    expect(header).toHaveAttribute("data-indeterminate", "true");
  });

  it("is checked when every visible row is selected", () => {
    const { header } = renderTable(["P1-1", ...VISIBLE]);

    expect(header).toBeChecked();
    expect(header).toHaveAttribute("data-indeterminate", "false");
  });

  it("selects the visible rows when clicked while only off-page issues are selected", () => {
    const { header, onSelectAll } = renderTable(["P1-1", "P1-2", "P1-3"]);

    fireEvent.click(header);

    expect(onSelectAll).toHaveBeenCalledTimes(1);
    expect(onSelectAll).toHaveBeenCalledWith(true, VISIBLE);
  });
});
