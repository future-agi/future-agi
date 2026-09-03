import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AnnotationLabelsView from "../view/annotation-labels-view";

// Mock Iconify
vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

// Mock AnnotationsTabs (uses react-router)
vi.mock("../../view/annotations-tabs", () => ({
  default: () => <div data-testid="annotations-tabs">Tabs</div>,
}));

// Mock fDate
vi.mock("src/utils/format-time", () => ({
  fDate: () => "Jan 1, 2025",
}));

// Mock notistack
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Mock useQueryClient (used by ArchiveLabelDialog)
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

// Mock axios (used by ArchiveLabelDialog undo action)
vi.mock("src/utils/axios", () => ({ default: { post: vi.fn() } }));

// Mock useAgTheme (used by AnnotationLabelTable)
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
  onCellClicked: _onCellClicked,
  noRowsOverlayComponent: NoRowsOverlay,
}) {
  if (!rowData || rowData.length === 0) {
    return NoRowsOverlay ? <NoRowsOverlay /> : null;
  }
  return (
    <div data-testid="ag-grid">
      {rowData.map((row) => (
        <div key={row.id} data-testid="ag-grid-row">
          {columnDefs.map((col) => {
            const Renderer = col.cellRenderer;
            return Renderer ? (
              <div key={col.field}>
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

// Mock CreateLabelDrawer to avoid deep dependency tree
vi.mock("../create-label-drawer", () => ({
  default: ({ open }) =>
    open ? <div data-testid="create-drawer">Create Label</div> : null,
}));

const MOCK_LABELS = [
  {
    id: "1",
    name: "Accuracy",
    type: "categorical",
    description: "Accuracy label",
    annotation_count: 2,
    created_at: "2025-01-01T00:00:00Z",
  },
  {
    id: "2",
    name: "Quality",
    type: "numeric",
    description: "",
    annotation_count: 0,
    created_at: "2025-01-02T00:00:00Z",
  },
];

// Mock the API hooks
const mockListData = {
  results: MOCK_LABELS,
  count: 2,
};

const mockArchiveLabelMutate = vi.hoisted(() => vi.fn());
const mockRestoreLabelMutate = vi.hoisted(() => vi.fn());

vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  useAnnotationLabelsList: vi.fn(() => ({
    data: mockListData,
    isLoading: false,
  })),
  useCreateAnnotationLabel: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAnnotationLabel: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteAnnotationLabel: () => ({
    mutate: mockArchiveLabelMutate,
    isPending: false,
  }),
  useRestoreAnnotationLabel: () => ({
    mutate: mockRestoreLabelMutate,
    isPending: false,
  }),
  annotationLabelEndpoints: { restore: () => "/api/labels/restore" },
  annotationLabelKeys: { all: ["annotation-labels"] },
}));

// Mock settings sub-components
vi.mock("../settings/categorical-settings", () => ({
  default: () => <div data-testid="categorical-settings" />,
}));
vi.mock("../settings/numeric-settings", () => ({
  default: () => <div data-testid="numeric-settings" />,
}));
vi.mock("../settings/text-settings", () => ({
  default: () => <div data-testid="text-settings" />,
}));
vi.mock("../settings/star-settings", () => ({
  default: () => <div data-testid="star-settings" />,
}));

describe("AnnotationLabelsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders tabs and Create Label button", () => {
    render(<AnnotationLabelsView />);

    expect(screen.getByTestId("annotations-tabs")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /create label/i }),
    ).toBeInTheDocument();
  });

  it("renders tabs", () => {
    render(<AnnotationLabelsView />);
    expect(screen.getByTestId("annotations-tabs")).toBeInTheDocument();
  });

  it("renders label table with data", () => {
    render(<AnnotationLabelsView />);

    expect(screen.getByText("Accuracy")).toBeInTheDocument();
    expect(screen.getByText("Quality")).toBeInTheDocument();
  });

  it("renders search field and type filter", () => {
    render(<AnnotationLabelsView />);

    expect(screen.getByPlaceholderText("Search labels...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Active" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archived" }),
    ).toBeInTheDocument();
    // Type filter select is rendered (MUI renders select with a combobox role)
    const selects = screen.getAllByRole("combobox");
    expect(selects.length).toBeGreaterThanOrEqual(1);
  });

  it("opens create drawer on Create Label click", async () => {
    const user = userEvent.setup();
    render(<AnnotationLabelsView />);

    await user.click(screen.getByRole("button", { name: /create label/i }));

    // Drawer mock renders "Create Label" text when open
    await waitFor(() => {
      expect(screen.getByTestId("create-drawer")).toBeInTheDocument();
    });
  });

  it("shows empty state when no data and no filters", async () => {
    const { useAnnotationLabelsList } = await import(
      "src/api/annotation-labels/annotation-labels"
    );
    useAnnotationLabelsList.mockReturnValue({
      data: { results: [], count: 0 },
      isLoading: false,
    });

    render(<AnnotationLabelsView />);

    expect(screen.getByText("No labels created yet")).toBeInTheDocument();
  });

  it("loads archived labels and restores from the archived view", async () => {
    const user = userEvent.setup();
    const { useAnnotationLabelsList } = await import(
      "src/api/annotation-labels/annotation-labels"
    );
    useAnnotationLabelsList.mockReturnValue({
      data: {
        results: [{ ...MOCK_LABELS[0], archived: true }],
        count: 1,
      },
      isLoading: false,
    });

    render(<AnnotationLabelsView />);

    await user.click(screen.getByRole("button", { name: "Archived" }));

    await waitFor(() => {
      expect(useAnnotationLabelsList).toHaveBeenLastCalledWith(
        expect.objectContaining({
          archived: true,
          page: 1,
        }),
      );
    });

    await user.click(
      screen.getByRole("button", { name: /restore label actions/i }),
    );
    await user.click(screen.getByRole("menuitem", { name: /restore/i }));

    expect(mockRestoreLabelMutate).toHaveBeenCalledWith("1");
  });
});
