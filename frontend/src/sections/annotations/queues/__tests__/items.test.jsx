/**
 * Phase 2A – Queue Items component tests.
 * Tests: ItemStatusBadge, SourceBadge, QueueItemsEmpty, QueueItemsTable
 */
import PropTypes from "prop-types";
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ItemStatusBadge from "../items/item-status-badge";
import SourceBadge from "../items/source-badge";
import QueueItemsEmpty from "../items/queue-items-empty";
import QueueItemsTable from "../items/queue-items-table";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/utils/format-time", () => ({
  fToNow: () => "3 hours ago",
}));

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgTheme: () => ({ withParams: () => ({}) }),
  useAgThemeWith: () => ({}),
}));

vi.mock("src/styles/clean-data-table.css", () => ({}));

// Mock AG Grid
function MockAgGridReact({
  rowData,
  columnDefs,
  context,
  onCellClicked,
  noRowsOverlayComponent: NoRowsOverlay,
  rowSelection,
  selectionColumnDef,
  onSelectionChanged,
}) {
  if (!rowData || rowData.length === 0) {
    return NoRowsOverlay ? <NoRowsOverlay /> : null;
  }
  return (
    <div data-testid="ag-grid">
      {rowSelection && selectionColumnDef && (
        <div
          data-testid="selection-column-def"
          data-width={selectionColumnDef.width}
          data-min-width={selectionColumnDef.minWidth}
          data-max-width={selectionColumnDef.maxWidth}
        />
      )}
      <div data-testid="ag-grid-header">
        {rowSelection && <input type="checkbox" aria-label="select-all" />}
        {columnDefs
          .filter((c) => c.headerName)
          .map((col) => (
            <span key={col.field}>{col.headerName}</span>
          ))}
      </div>
      {rowData.map((row) => (
        <div key={row.id} data-testid="ag-grid-row">
          {rowSelection && (
            <input
              type="checkbox"
              aria-label={`select-${row.id}`}
              onChange={() => {
                if (onSelectionChanged) {
                  onSelectionChanged({
                    api: {
                      getSelectedNodes: () => [{ data: row }],
                    },
                  });
                }
              }}
            />
          )}
          {columnDefs.map((col) => {
            const Renderer = col.cellRenderer;
            return Renderer ? (
              <div
                key={col.field}
                onClick={() => {
                  if (col.field !== "actions" && onCellClicked) {
                    onCellClicked({
                      data: row,
                      column: { getColId: () => col.field },
                    });
                  }
                }}
              >
                <Renderer data={row} context={context} />
              </div>
            ) : null;
          })}
        </div>
      ))}
    </div>
  );
}

MockAgGridReact.propTypes = {
  rowData: PropTypes.array,
  columnDefs: PropTypes.array.isRequired,
  context: PropTypes.object,
  onCellClicked: PropTypes.func,
  noRowsOverlayComponent: PropTypes.elementType,
  rowSelection: PropTypes.object,
  selectionColumnDef: PropTypes.object,
  onSelectionChanged: PropTypes.func,
};

vi.mock("ag-grid-react", () => ({
  AgGridReact: MockAgGridReact,
}));

// ---------------------------------------------------------------------------
// ItemStatusBadge
// ---------------------------------------------------------------------------
describe("ItemStatusBadge", () => {
  it.each([
    ["pending", "Pending"],
    ["in_progress", "In Progress"],
    ["in_review", "In Review"],
    ["completed", "Completed"],
    ["skipped", "Skipped"],
  ])("renders %s status as '%s'", (status, label) => {
    render(<ItemStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("falls back to Pending for unknown status", () => {
    render(<ItemStatusBadge status="nope" />);
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// SourceBadge
// ---------------------------------------------------------------------------
describe("SourceBadge", () => {
  it.each([
    ["dataset_row", "Dataset Row"],
    ["trace", "Trace"],
    ["observation_span", "Span"],
    ["prototype_run", "Prototype"],
    ["call_execution", "Simulation"],
  ])("renders %s source as '%s'", (type, label) => {
    render(<SourceBadge sourceType={type} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("renders unknown source type as-is", () => {
    render(<SourceBadge sourceType="custom_type" />);
    expect(screen.getByText("custom_type")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// QueueItemsEmpty
// ---------------------------------------------------------------------------
describe("QueueItemsEmpty", () => {
  it("renders heading and description", () => {
    render(<QueueItemsEmpty onAddClick={() => {}} />);
    expect(screen.getByText("No items in this queue")).toBeInTheDocument();
    expect(screen.getByText(/Add items from datasets/)).toBeInTheDocument();
  });

  it("calls onAddClick when Add Items is clicked", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<QueueItemsEmpty onAddClick={onClick} />);
    await user.click(screen.getByRole("button", { name: /add items/i }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("hides add action when the user cannot manage queue items", () => {
    render(<QueueItemsEmpty />);
    expect(
      screen.getByText("A queue manager can add items to this queue."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /add items/i }),
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// QueueItemsTable
// ---------------------------------------------------------------------------
const MOCK_ITEMS = [
  {
    id: "item-1",
    source_type: "dataset_row",
    source_preview: {
      type: "dataset_row",
      dataset_name: "My Dataset",
      row_order: 0,
    },
    status: "pending",
    assigned_to_name: null,
    assigned_users: [],
    review_status: null,
    created_at: "2025-01-01T00:00:00Z",
  },
  {
    id: "item-2",
    source_type: "trace",
    source_preview: {
      type: "trace",
      name: "Hello trace",
    },
    status: "completed",
    assigned_to_name: "Alice",
    assigned_users: [{ id: "user-1", name: "Alice" }],
    review_status: "approved",
    created_at: "2025-01-02T00:00:00Z",
  },
];

const tableProps = {
  data: MOCK_ITEMS,
  loading: false,
  page: 0,
  rowsPerPage: 10,
  totalCount: 2,
  onPageChange: vi.fn(),
  onRowsPerPageChange: vi.fn(),
  selectedIds: new Set(),
  onSelectToggle: vi.fn(),
  onSelectAll: vi.fn(),
  onRemove: vi.fn(),
};

describe("QueueItemsTable", () => {
  it("renders table headers", () => {
    render(<QueueItemsTable {...tableProps} />);
    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("Preview")).toBeInTheDocument();
    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.getByText("Assigned To")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.queryByText("Latency")).not.toBeInTheDocument();
    expect(screen.queryByText("Response Time")).not.toBeInTheDocument();
    expect(screen.queryByText("Duration")).not.toBeInTheDocument();
  });

  it("renders item rows with source badges and previews", () => {
    render(<QueueItemsTable {...tableProps} />);
    expect(screen.getByText("Dataset Row")).toBeInTheDocument();
    expect(screen.getByText("Trace")).toBeInTheDocument();
    expect(screen.getByText(/My Dataset - Row 0/)).toBeInTheDocument();
    expect(screen.getByText("Hello trace")).toBeInTheDocument();
  });

  it("renders status badges", () => {
    render(<QueueItemsTable {...tableProps} />);
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("shows pending review items as in review in the workflow status", () => {
    render(
      <QueueItemsTable
        {...tableProps}
        data={[
          {
            ...MOCK_ITEMS[0],
            status: "in_progress",
            review_status: "pending_review",
          },
        ]}
        totalCount={1}
      />,
    );
    expect(screen.getByText("In Review")).toBeInTheDocument();
  });

  it("hides selection and remove controls for non-managers", () => {
    render(<QueueItemsTable {...tableProps} canManageItems={false} />);
    expect(screen.queryByLabelText("select-all")).not.toBeInTheDocument();
    expect(
      screen
        .queryAllByTestId("iconify")
        .some((el) => el.getAttribute("data-icon") === "mingcute:close-line"),
    ).toBe(false);
  });

  it("shows assigned-to avatars or assign chip", () => {
    render(<QueueItemsTable {...tableProps} />);
    expect(screen.getByText("+ Assign")).toBeInTheDocument();
    // Assigned user shows as avatar with initials
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("does not show source metrics in the queue item list", () => {
    render(<QueueItemsTable {...tableProps} />);
    expect(screen.queryByText("120ms")).not.toBeInTheDocument();
    expect(screen.queryByText("240ms")).not.toBeInTheDocument();
  });

  it("shows all annotators in auto-assign mode", () => {
    render(
      <QueueItemsTable
        {...tableProps}
        autoAssign
        annotators={[
          { user_id: "user-1", name: "Alice", role: "annotator" },
          { user_id: "user-2", name: "Bob", role: "annotator" },
          { user_id: "user-3", name: "Reviewer", role: "reviewer" },
        ]}
      />,
    );
    expect(screen.getAllByText("All annotators").length).toBeGreaterThan(0);
    expect(screen.queryByText("+ Assign")).not.toBeInTheDocument();
    expect(screen.queryByText("Reviewer")).not.toBeInTheDocument();
  });

  it("shows the annotator name in auto-assign mode when only one annotator exists", () => {
    render(
      <QueueItemsTable
        {...tableProps}
        autoAssign
        annotators={[
          { user_id: "user-1", name: "Alice", role: "annotator" },
          { user_id: "user-3", name: "Reviewer", role: "reviewer" },
        ]}
      />,
    );
    expect(screen.getAllByText("Alice").length).toBeGreaterThan(0);
    expect(screen.queryByText("All annotators")).not.toBeInTheDocument();
    expect(screen.queryByText("+ Assign")).not.toBeInTheDocument();
    expect(screen.queryByText("Reviewer")).not.toBeInTheDocument();
  });

  it("shows review status chip when present", () => {
    render(<QueueItemsTable {...tableProps} />);
    expect(screen.getByText("approved")).toBeInTheDocument();
  });

  it("shows loading skeletons when loading", () => {
    const { container } = render(
      <QueueItemsTable {...tableProps} loading={true} />,
    );
    expect(screen.queryByText("My Dataset")).not.toBeInTheDocument();
    expect(
      container.querySelectorAll(".MuiSkeleton-root").length,
    ).toBeGreaterThan(0);
  });

  it("shows empty message when no data", () => {
    render(<QueueItemsTable {...tableProps} data={[]} />);
    expect(screen.getByText("No items match your filters")).toBeInTheDocument();
  });

  it("calls onRemove after confirmation when remove button is clicked", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    render(<QueueItemsTable {...tableProps} onRemove={onRemove} />);

    // Click the close/remove icon – opens confirmation dialog
    const removeIcons = screen
      .getAllByTestId("iconify")
      .filter((el) => el.getAttribute("data-icon") === "mingcute:close-line");
    await user.click(removeIcons[0].closest("button"));

    // Confirm removal in the dialog
    await user.click(screen.getByRole("button", { name: /^remove$/i }));
    expect(onRemove).toHaveBeenCalledWith(MOCK_ITEMS[0]);
  });

  it("calls onSelectToggle when row checkbox is clicked", async () => {
    const user = userEvent.setup();
    const onSelectToggle = vi.fn();
    render(<QueueItemsTable {...tableProps} onSelectToggle={onSelectToggle} />);

    // Row checkboxes rendered by mock AG Grid
    const checkboxes = screen.getAllByRole("checkbox");
    // First checkbox is select-all, subsequent ones are per-row
    await user.click(checkboxes[1]);
    expect(onSelectToggle).toHaveBeenCalledWith("item-1");
  });

  it("keeps the selection checkbox column at a stable width", () => {
    render(<QueueItemsTable {...tableProps} />);

    const selectionColumn = screen.getByTestId("selection-column-def");
    expect(selectionColumn).toHaveAttribute("data-width", "44");
    expect(selectionColumn).toHaveAttribute("data-min-width", "44");
    expect(selectionColumn).toHaveAttribute("data-max-width", "44");
  });
});
