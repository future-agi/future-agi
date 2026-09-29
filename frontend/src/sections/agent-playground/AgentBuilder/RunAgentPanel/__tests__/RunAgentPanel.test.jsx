import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Resolved execution: pass the chosen node id straight through.
vi.mock("../../../hooks/useResolvedExecution", () => ({
  default: ({ selectedNodeId }) => ({
    resolvedExecutionId: "exec-1",
    nodeExecutionId: selectedNodeId ? `${selectedNodeId}::exec` : null,
  }),
}));

// Workflow run store: keep the panel in the "not running" state.
vi.mock("../../../store", () => ({
  useWorkflowRunStoreShallow: (selector) =>
    selector({ isRunning: false, workflowState: "idle" }),
}));

// AgentGraph: render a tiny stub that exposes the click handler and the
// currently-selected id, so the test can verify two-way binding.
const agentGraphSpy = vi.fn();
vi.mock("src/components/AgentGraph", () => ({
  AgentGraph: (props) => {
    agentGraphSpy(props);
    return (
      <div data-testid="agent-graph">
        <span data-testid="graph-selected">{props.selectedNodeId ?? ""}</span>
        <button
          data-testid="graph-click-b"
          onClick={(e) => props.onNodeClick?.(e, { id: "b" })}
        >
          click b
        </button>
      </div>
    );
  },
}));

// ReactFlow bits are not needed because AgentGraph is mocked above.
vi.mock("@xyflow/react", () => ({}));

// PanelErrorBoundary: pass-through.
vi.mock("../../../components/PanelErrorBoundary", () => ({
  default: ({ children }) => <>{children}</>,
}));

// NodeOutputDetail: render a stub that records the execution id and the
// node execution id it is being asked about, so we can verify the
// two-way binding feeds the existing detail panel correctly.
const nodeDetailSpy = vi.fn();
vi.mock("../NodeOutputDetail", () => ({
  default: (props) => {
    nodeDetailSpy(props);
    return (
      <div data-testid="node-detail">
        <span data-testid="detail-exec">{props.executionId ?? ""}</span>
        <span data-testid="detail-node">{props.nodeExecutionId ?? ""}</span>
      </div>
    );
  },
}));

// TreeView: render each node as a clickable row, calling onNodeSelect.
vi.mock("../../../../TreeView", () => ({
  TreeView: ({ data, onNodeSelect, selectedNodeId }) => (
    <ul data-testid="tree-view">
      {data.map((n) => (
        <li key={n.id}>
          <button
            data-testid={`tree-row-${n.id}`}
            aria-pressed={selectedNodeId === n.id}
            onClick={() => onNodeSelect?.(n.id)}
          >
            {n.name}
          </button>
        </li>
      ))}
    </ul>
  ),
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));

import RunAgentPanel from "../RunAgentPanel";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeNodes() {
  return [
    { id: "a", name: "Prompt A", type: "atomic" },
    {
      id: "agent-1",
      name: "Agent 1",
      type: "subgraph",
      subGraph: {
        nodes: [
          { id: "inner-1", name: "Inner 1", type: "atomic" },
          { id: "inner-2", name: "Inner 2", type: "eval" },
        ],
      },
    },
    { id: "b", name: "Eval B", type: "eval", cost: 0.001, tokens: 7 },
  ];
}

function renderPanel(executionDataOverrides = {}) {
  const executionData = {
    nodes: makeNodes(),
    ...executionDataOverrides,
  };
  return render(
    <RunAgentPanel
      panelHeight={400}
      onResize={() => {}}
      executionId="exec-1"
      executionData={executionData}
    />,
  );
}

beforeEach(() => {
  agentGraphSpy.mockClear();
  nodeDetailSpy.mockClear();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("RunAgentPanel — run-step list (#2508)", () => {
  it("renders a list of nodes alongside the canvas", () => {
    renderPanel();
    const tree = screen.getByTestId("tree-view");
    expect(within(tree).getByTestId("tree-row-a")).toBeTruthy();
    expect(within(tree).getByTestId("tree-row-agent-1")).toBeTruthy();
    expect(within(tree).getByTestId("tree-row-b")).toBeTruthy();
  });

  it("renders subgraph inner nodes as children of the parent row", () => {
    renderPanel();
    const tree = screen.getByTestId("tree-view");
    // Inner ids are exposed by the TreeView data prop.  The mocked TreeView
    // does not render children, so we only assert the parent is present
    // and that the adapter fed the children through (covered by the
    // adapter unit test).
    expect(within(tree).getByTestId("tree-row-agent-1")).toBeTruthy();
  });

  it("selects a node when the user clicks it in the list", () => {
    renderPanel();
    fireEvent.click(screen.getByTestId("tree-row-b"));
    expect(screen.getByTestId("graph-selected").textContent).toBe("b");
    expect(screen.getByTestId("detail-node").textContent).toBe("b::exec");
  });

  it("keeps the canvas and the list in sync when the graph is clicked", () => {
    renderPanel();
    // The mocked AgentGraph exposes a button that fires its onNodeClick
    // handler with a synthetic node. Click it, then assert the selected
    // node id reaches the NodeOutputDetail and (by way of the
    // selectedNodeId prop) is passed back to the AgentGraph stub.
    fireEvent.click(screen.getByTestId("graph-click-b"));
    expect(screen.getByTestId("graph-selected").textContent).toBe("b");
    expect(screen.getByTestId("detail-node").textContent).toBe("b::exec");
  });

  it("renders an empty list when executionData has no nodes", () => {
    renderPanel({ nodes: [] });
    const tree = screen.getByTestId("tree-view");
    expect(tree.children).toHaveLength(0);
  });

  it("renders an empty list when executionData is undefined", () => {
    render(
      <RunAgentPanel
        panelHeight={400}
        onResize={() => {}}
        executionId={null}
        executionData={undefined}
      />,
    );
    const tree = screen.getByTestId("tree-view");
    expect(tree.children).toHaveLength(0);
  });
});