import PropTypes from "prop-types";
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AnnotationLabelTable from "../annotation-label-table";

// Mock Iconify
function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}

MockIconify.propTypes = {
  icon: PropTypes.string.isRequired,
};

vi.mock("src/components/iconify", () => ({
  default: MockIconify,
}));

// Mock fDate
vi.mock("src/utils/format-time", () => ({
  fDate: (date) => (date ? "Jan 1, 2025" : "-"),
}));

// Mock useAgTheme
vi.mock("src/hooks/use-ag-theme", () => ({
  useAgTheme: () => ({
    withParams: () => ({}),
  }),
  useAgThemeWith: () => ({}),
}));

// Mock AG Grid
function MockAgGridReact({
  rowData,
  columnDefs,
  context,
  onCellClicked,
  noRowsOverlayComponent: NoRowsOverlay,
}) {
  if (!rowData || rowData.length === 0) {
    return NoRowsOverlay ? <NoRowsOverlay /> : <div>No rows</div>;
  }
  return (
    <div data-testid="ag-grid">
      <div data-testid="ag-grid-header">
        {columnDefs.map((col) => (
          <span key={col.field}>{col.headerName}</span>
        ))}
      </div>
      {rowData.map((row) => (
        <div key={row.id} data-testid="ag-grid-row">
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
};

vi.mock("ag-grid-react", () => ({
  AgGridReact: MockAgGridReact,
}));

const MOCK_LABELS = [
  {
    id: "1",
    name: "Accuracy",
    type: "categorical",
    description: "Measures response accuracy",
    annotation_count: 3,
    created_at: "2025-01-01T00:00:00Z",
  },
  {
    id: "2",
    name: "Quality Score",
    type: "numeric",
    description: "Numeric quality score",
    annotation_count: 0,
    created_at: "2025-01-02T00:00:00Z",
  },
  {
    id: "3",
    name: "Feedback",
    type: "text",
    description: "",
    annotation_count: 1,
    created_at: "2025-01-03T00:00:00Z",
  },
];

const defaultProps = {
  data: MOCK_LABELS,
  loading: false,
  page: 0,
  rowsPerPage: 10,
  totalCount: 3,
  onPageChange: vi.fn(),
  onRowsPerPageChange: vi.fn(),
  onEdit: vi.fn(),
  onDuplicate: vi.fn(),
  onArchive: vi.fn(),
};

describe("AnnotationLabelTable", () => {
  it("renders table headers", () => {
    render(<AnnotationLabelTable {...defaultProps} />);

    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Type")).toBeInTheDocument();
    expect(screen.getByText("Description")).toBeInTheDocument();
    expect(screen.getByText("Used In")).toBeInTheDocument();
    expect(screen.getByText("Created")).toBeInTheDocument();
  });

  it("renders label rows", () => {
    render(<AnnotationLabelTable {...defaultProps} />);

    expect(screen.getByText("Accuracy")).toBeInTheDocument();
    expect(screen.getByText("Quality Score")).toBeInTheDocument();
    expect(screen.getByText("Feedback")).toBeInTheDocument();
  });

  it("renders type chips with correct labels", () => {
    render(<AnnotationLabelTable {...defaultProps} />);

    expect(screen.getByText("Categorical")).toBeInTheDocument();
    expect(screen.getByText("Numeric")).toBeInTheDocument();
    expect(screen.getByText("Text")).toBeInTheDocument();
  });

  it("shows no rows message when data is empty", () => {
    render(<AnnotationLabelTable {...defaultProps} data={[]} totalCount={0} />);

    expect(
      screen.getByText("No labels match your filters."),
    ).toBeInTheDocument();
  });

  it("shows description or dash for empty description", () => {
    render(<AnnotationLabelTable {...defaultProps} />);

    expect(screen.getByText("Measures response accuracy")).toBeInTheDocument();
    expect(screen.getAllByText("-").length).toBeGreaterThanOrEqual(1);
  });

  it("renders backend annotation_count in the Used In column", () => {
    render(
      <AnnotationLabelTable
        {...defaultProps}
        data={[
          {
            id: "contract-label",
            name: "Contract label",
            type: "text",
            annotation_count: 7,
            created_at: "2025-01-04T00:00:00Z",
          },
        ]}
        totalCount={1}
      />,
    );

    expect(screen.getByText("7")).toBeInTheDocument();
  });

  describe("action menu", () => {
    it("opens menu on more button click", async () => {
      const user = userEvent.setup();
      render(<AnnotationLabelTable {...defaultProps} />);

      const moreButtons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) => el.getAttribute("data-icon") === "eva:more-vertical-fill",
        );
      await user.click(moreButtons[0].closest("button"));

      expect(screen.getByText("Edit")).toBeInTheDocument();
      expect(screen.getByText("Duplicate")).toBeInTheDocument();
      expect(screen.getByText("Archive")).toBeInTheDocument();
    });

    it("calls onEdit with row data", async () => {
      const user = userEvent.setup();
      const onEdit = vi.fn();
      render(<AnnotationLabelTable {...defaultProps} onEdit={onEdit} />);

      const moreButtons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) => el.getAttribute("data-icon") === "eva:more-vertical-fill",
        );
      await user.click(moreButtons[0].closest("button"));
      await user.click(screen.getByText("Edit"));

      expect(onEdit).toHaveBeenCalledWith(MOCK_LABELS[0]);
    });

    it("calls onDuplicate with row data", async () => {
      const user = userEvent.setup();
      const onDuplicate = vi.fn();
      render(
        <AnnotationLabelTable {...defaultProps} onDuplicate={onDuplicate} />,
      );

      const moreButtons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) => el.getAttribute("data-icon") === "eva:more-vertical-fill",
        );
      await user.click(moreButtons[0].closest("button"));
      await user.click(screen.getByText("Duplicate"));

      expect(onDuplicate).toHaveBeenCalledWith(MOCK_LABELS[0]);
    });

    it("calls onArchive with row data", async () => {
      const user = userEvent.setup();
      const onArchive = vi.fn();
      render(<AnnotationLabelTable {...defaultProps} onArchive={onArchive} />);

      const moreButtons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) => el.getAttribute("data-icon") === "eva:more-vertical-fill",
        );
      await user.click(moreButtons[0].closest("button"));
      await user.click(screen.getByText("Archive"));

      expect(onArchive).toHaveBeenCalledWith(MOCK_LABELS[0]);
    });
  });
});
