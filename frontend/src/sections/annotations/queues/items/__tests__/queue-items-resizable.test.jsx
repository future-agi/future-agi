import PropTypes from "prop-types";
import { describe, it, expect, vi } from "vitest";
import { render } from "src/utils/test-utils";
import QueueItemsTable from "../queue-items-table";

// Capture what the table passes to AG Grid so the column-definition flags
// can be asserted without rendering a real grid.
let capturedProps = {};

function MockAgGridReact({ rowData, columnDefs, defaultColDef, ...rest }) {
  capturedProps = { rowData, columnDefs, defaultColDef, ...rest };
  return (
    <div data-testid="ag-grid">
      {(rowData || []).map((row) => (
        <div key={row.id} data-testid="ag-grid-row" />
      ))}
    </div>
  );
}

MockAgGridReact.propTypes = {
  rowData: PropTypes.array,
  columnDefs: PropTypes.array,
  defaultColDef: PropTypes.object,
};

vi.mock("ag-grid-react", () => ({
  AgGridReact: MockAgGridReact,
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => () => ({}),
}));

vi.mock("src/styles/clean-data-table.css", () => ({}));

vi.mock("src/utils/format-time", () => ({
  fToNow: () => "2 days ago",
}));

vi.mock("../annotation-queue-table", () => ({
  getInitials: (name) => (name || "?").slice(0, 2).toUpperCase(),
}));

vi.mock("../constants", () => ({
  isQueueAnnotatorRole: () => false,
}));

vi.mock("./source-badge", () => ({
  default: () => null,
}));

vi.mock("./item-status-badge", () => ({
  default: () => null,
}));

const MOCK_ITEMS = [
  {
    id: "item-1",
    status: "pending_review",
    created_at: "2026-01-01T00:00:00Z",
    comment_count: 0,
    source: { type: "dataset_row", dataset_name: "DS", row_order: 1 },
    assignee: null,
  },
];

function renderTable() {
  return render(
    <QueueItemsTable data={MOCK_ITEMS} annotators={[{ id: "a1" }]} />,
  );
}

describe("QueueItemsTable column resizing (issue #1966)", () => {
  it("opts the grid into column resizing via defaultColDef", () => {
    renderTable();
    expect(capturedProps.defaultColDef.resizable).toBe(true);
  });

  it("keeps every non-actions data column without a per-column opt-out", () => {
    renderTable();
    for (const col of capturedProps.columnDefs) {
      if (col.field === "actions") continue;
      expect(
        col.resizable,
        `column "${col.field}" pins resizable=${col.resizable}`,
      ).toBeUndefined();
    }
  });

  it("keeps the actions column fixed via its own per-column override", () => {
    renderTable();
    const actions = capturedProps.columnDefs.find((c) => c.field === "actions");
    expect(actions.resizable).toBe(false);
  });
});
