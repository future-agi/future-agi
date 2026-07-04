/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ExperimentDetailDrawerContent from "../ExperimentDetailDrawerContent";

vi.mock("ag-grid-react", () => ({
  AgGridReact: ({ rowData = [] }) => (
    <div data-testid="ag-grid">
      {rowData.map((row, index) => (
        <div key={row?.id ?? index} data-testid="ag-grid-row">
          {row?.group?.name}:{row?.scoreValue?.cellValue ?? row?.scoreValue}
        </div>
      ))}
    </div>
  ),
}));

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => "ag-theme-test",
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/components/svg-color/svg-color", () => ({
  default: ({ src, alt, ...props }) => (
    <span data-testid="svg-color" data-src={src} aria-label={alt} {...props} />
  ),
}));

vi.mock("src/sections/common/DatapointCard", () => ({
  default: () => <div data-testid="datapoint-card" />,
}));

vi.mock("src/components/custom-audio/AudioDatapointCard", () => ({
  default: () => <div data-testid="audio-datapoint-card" />,
}));

vi.mock("src/sections/common/ImageDatapointCard", () => ({
  default: () => <div data-testid="image-datapoint-card" />,
}));

vi.mock("../AgentFlowRenderer", () => ({
  default: () => <div data-testid="agent-flow-renderer" />,
}));

vi.mock("../ViewDetailsModal", () => ({
  default: () => <div data-testid="view-details-modal" />,
}));

vi.mock(
  "src/sections/develop-detail/DataTab/AddEvaluationFeeback/AddEvaluationFeeback",
  () => ({
    default: () => <div data-testid="add-evaluation-feedback" />,
  }),
);

vi.mock("src/sections/develop-detail/states", () => ({
  useAddEvaluationFeebackStore: () => ({
    setAddEvaluationFeeback: vi.fn(),
  }),
}));

const baseProps = {
  onClose: vi.fn(),
  row: { rowId: "row-123", cell: { cellValue: "value" } },
  columnConfig: [],
  showDiff: false,
  setShowDiff: vi.fn(),
  handleToggleDiff: vi.fn(),
  nextRowId: false,
  prevRowId: false,
  handleFetchNextRow: vi.fn(),
  handleFetchPrevRow: vi.fn(),
  isPending: false,
  handleRefetchRowData: vi.fn(),
  refreshGrid: vi.fn(),
};

const renderDrawer = (props = {}) =>
  render(<ExperimentDetailDrawerContent {...baseProps} {...props} />);

describe("ExperimentDetailDrawerContent row identifier Unit", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the current row id in the drawer header", () => {
    renderDrawer();

    expect(screen.getByText("Experiments")).toBeInTheDocument();
    expect(screen.getByText("Row ID: row-123")).toBeInTheDocument();
    expect(screen.getByLabelText("Row ID: row-123")).toBeInTheDocument();
    expect(screen.getByTestId("experiment-row-id-chip")).toBeInTheDocument();
  });

  it("shows a numeric row id in the drawer header", () => {
    renderDrawer({ row: { rowId: 0, cell: { cellValue: "value" } } });

    expect(screen.getByText("Row ID: 0")).toBeInTheDocument();
  });

  it.each([undefined, null, ""])(
    "does not render the row id chip when the row id is %s",
    (rowId) => {
      renderDrawer({ row: { rowId, cell: { cellValue: "value" } } });

      expect(screen.getByText("Experiments")).toBeInTheDocument();
      expect(
        screen.queryByTestId("experiment-row-id-chip"),
      ).not.toBeInTheDocument();
    },
  );

  it("does not render the row id chip when the row is unavailable", () => {
    renderDrawer({ row: null });

    expect(
      screen.queryByTestId("experiment-row-id-chip"),
    ).not.toBeInTheDocument();
  });

  it("updates the visible row id when the row changes", () => {
    const { rerender } = renderDrawer({ row: { rowId: "row-before" } });

    expect(screen.getByText("Row ID: row-before")).toBeInTheDocument();

    rerender(
      <ExperimentDetailDrawerContent
        {...baseProps}
        row={{ rowId: "row-after" }}
      />,
    );

    expect(screen.queryByText("Row ID: row-before")).not.toBeInTheDocument();
    expect(screen.getByText("Row ID: row-after")).toBeInTheDocument();
  });

  it("updates evaluation score row data when the row changes", () => {
    const columnConfig = [
      {
        id: "input",
        name: "Input",
        datasetId: "dataset-1",
        isBaseColumn: true,
        group: {
          id: "dataset-group",
          name: "Dataset",
          origin: "Dataset",
        },
      },
      {
        id: "eval-score",
        name: "Accuracy",
        datasetId: "dataset-1",
        originType: "evaluation",
        group: {
          id: "evaluation-group",
          name: "Accuracy",
        },
      },
    ];

    const { rerender } = renderDrawer({
      columnConfig,
      row: {
        rowId: "row-before",
        input: { cellValue: "prompt" },
        "eval-score": { cellValue: "pass" },
      },
    });

    expect(screen.getByText("Accuracy:pass")).toBeInTheDocument();

    rerender(
      <ExperimentDetailDrawerContent
        {...baseProps}
        columnConfig={columnConfig}
        row={{
          rowId: "row-after",
          input: { cellValue: "prompt" },
          "eval-score": { cellValue: "fail" },
        }}
      />,
    );

    expect(screen.queryByText("Accuracy:pass")).not.toBeInTheDocument();
    expect(screen.getByText("Accuracy:fail")).toBeInTheDocument();
  });
});
