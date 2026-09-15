import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import EvalPlayground from "../EvalPlayground";

const refreshServerSideMock = vi.hoisted(() => vi.fn());

// TopEvaluateSection and BottomEvaluationSection pull in forms, react-query,
// Monaco and the logs grid — way too heavy for a test that only cares about
// how EvalPlayground wires evaluation/refreshGrid/selectedData/leftWidth
// between its children. Stub them down to plain buttons that expose the
// props EvalPlayground passes in. Declared as hoisted function declarations
// (not const arrow functions) so they're safe to reference from the (also
// hoisted) vi.mock factories below.
function MockTopEvaluateSection({ evaluation, refreshGrid, setSelectedData }) {
  return (
    <div data-testid="top-section">
      <span data-testid="top-evaluation-name">{evaluation?.name}</span>
      <button type="button" onClick={refreshGrid}>
        refresh
      </button>
      <button type="button" onClick={() => setSelectedData({ model: "gpt-4" })}>
        select-model
      </button>
    </div>
  );
}

MockTopEvaluateSection.propTypes = {
  evaluation: PropTypes.object,
  refreshGrid: PropTypes.func,
  setSelectedData: PropTypes.func,
};

function MockBottomEvaluationSection({
  tableRef,
  evaluation,
  selectedData,
  setInitialLeftWidth,
}) {
  React.useEffect(() => {
    if (tableRef) {
      tableRef.current = { api: { refreshServerSide: refreshServerSideMock } };
    }
  }, [tableRef]);
  return (
    <div data-testid="bottom-section">
      <span data-testid="bottom-evaluation-id">{evaluation?.id}</span>
      <span data-testid="selected-data">{JSON.stringify(selectedData)}</span>
      <button type="button" onClick={() => setInitialLeftWidth(30)}>
        shrink
      </button>
    </div>
  );
}

MockBottomEvaluationSection.propTypes = {
  tableRef: PropTypes.object,
  evaluation: PropTypes.object,
  selectedData: PropTypes.object,
  setInitialLeftWidth: PropTypes.func,
};

function MockResizablePanels({
  leftPanel,
  rightPanel,
  initialLeftWidth,
  minLeftWidth,
  maxLeftWidth,
  orientation,
  showIcon,
}) {
  return (
    <div
      data-testid="resizable-panels"
      data-initial-left-width={initialLeftWidth}
      data-min-left-width={minLeftWidth}
      data-max-left-width={maxLeftWidth}
      data-orientation={orientation}
      data-show-icon={String(showIcon)}
    >
      <div data-testid="left-panel">{leftPanel}</div>
      <div data-testid="right-panel">{rightPanel}</div>
    </div>
  );
}

MockResizablePanels.propTypes = {
  leftPanel: PropTypes.node,
  rightPanel: PropTypes.node,
  initialLeftWidth: PropTypes.number,
  minLeftWidth: PropTypes.number,
  maxLeftWidth: PropTypes.number,
  orientation: PropTypes.string,
  showIcon: PropTypes.bool,
};

vi.mock("../TopEvaluationSection/TopEvaluateSection", () => ({
  default: MockTopEvaluateSection,
}));

vi.mock("../BottomEvaluationSection/BottomEvaluationSection", () => ({
  default: MockBottomEvaluationSection,
}));

vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: MockResizablePanels,
}));

const evaluation = { id: "eval-1", name: "Toxicity Check" };

describe("EvalPlayground", () => {
  beforeEach(() => {
    refreshServerSideMock.mockReset();
  });

  it("does not render its content when closed", () => {
    render(
      <EvalPlayground open={false} onClose={vi.fn()} evaluation={evaluation} />,
    );

    expect(screen.queryByText("Toxicity Check - Playground")).not.toBeInTheDocument();
  });

  it("renders the header with the evaluation name and wires the close button", () => {
    const onClose = vi.fn();
    render(<EvalPlayground open onClose={onClose} evaluation={evaluation} />);

    expect(screen.getByText("Toxicity Check - Playground")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("passes the evaluation down to both the top and bottom sections", () => {
    render(<EvalPlayground open onClose={vi.fn()} evaluation={evaluation} />);

    expect(screen.getByTestId("top-evaluation-name")).toHaveTextContent(
      "Toxicity Check",
    );
    expect(screen.getByTestId("bottom-evaluation-id")).toHaveTextContent(
      "eval-1",
    );
  });

  it("starts selectedData with a null model and threads updates from the top section to the bottom section", () => {
    render(<EvalPlayground open onClose={vi.fn()} evaluation={evaluation} />);

    expect(screen.getByTestId("selected-data")).toHaveTextContent(
      JSON.stringify({ model: null }),
    );

    fireEvent.click(screen.getByRole("button", { name: "select-model" }));

    expect(screen.getByTestId("selected-data")).toHaveTextContent(
      JSON.stringify({ model: "gpt-4" }),
    );
  });

  it("wires refreshGrid to call refreshServerSide on the grid api ref exposed by the bottom section", () => {
    render(<EvalPlayground open onClose={vi.fn()} evaluation={evaluation} />);

    fireEvent.click(screen.getByRole("button", { name: "refresh" }));

    expect(refreshServerSideMock).toHaveBeenCalledTimes(1);
    expect(refreshServerSideMock).toHaveBeenCalledWith({ force: true });
  });

  it("renders ResizablePanels with the expected layout configuration and a default 50/50 split", () => {
    render(<EvalPlayground open onClose={vi.fn()} evaluation={evaluation} />);

    const panels = screen.getByTestId("resizable-panels");
    expect(panels).toHaveAttribute("data-initial-left-width", "50");
    expect(panels).toHaveAttribute("data-min-left-width", "30");
    expect(panels).toHaveAttribute("data-max-left-width", "70");
    expect(panels).toHaveAttribute("data-orientation", "vertical");
    expect(panels).toHaveAttribute("data-show-icon", "true");
  });

  it("lets the bottom section shrink the left panel via setInitialLeftWidth", () => {
    render(<EvalPlayground open onClose={vi.fn()} evaluation={evaluation} />);

    fireEvent.click(screen.getByRole("button", { name: "shrink" }));

    expect(screen.getByTestId("resizable-panels")).toHaveAttribute(
      "data-initial-left-width",
      "30",
    );
  });

  it("renders without an evaluation without crashing", () => {
    render(<EvalPlayground open onClose={vi.fn()} evaluation={undefined} />);

    expect(screen.getByText("- Playground")).toBeInTheDocument();
    expect(screen.getByTestId("bottom-evaluation-id")).toHaveTextContent("");
  });
});
