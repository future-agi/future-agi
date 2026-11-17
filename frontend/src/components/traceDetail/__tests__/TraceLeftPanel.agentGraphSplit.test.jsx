import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import LeftPanelSplit from "../TraceLeftPanel";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../TraceTreeV2", () => ({
  default: () => <div data-testid="trace-tree" />,
}));
vi.mock("../SpanTreeTimeline", () => ({
  default: () => <div data-testid="span-timeline" />,
}));
vi.mock("src/sections/projects/LLMTracing/GraphSection/AgentGraph", () => ({
  default: () => <div data-testid="agent-graph" />,
}));
vi.mock("../buildTraceGraph", () => ({
  buildTraceGraph: () => ({
    nodes: [{ id: "agent" }],
    edges: [],
    nodeToSpanIds: { agent: ["agent"] },
  }),
}));

const spans = [
  {
    observation_span: {
      id: "agent",
      name: "invoke_agent",
      observation_type: "agent",
      status: "OK",
    },
    children: [],
  },
];

const onSelectSpan = vi.fn();
const setVisibleMetrics = vi.fn();
const panel = (showAgentGraph) => (
  <LeftPanelSplit
    viewMode="tree"
    spans={spans}
    selectedSpanId="agent"
    onSelectSpan={onSelectSpan}
    visibleMetrics={{}}
    setVisibleMetrics={setVisibleMetrics}
    showAgentGraph={showAgentGraph}
  />
);

// The split panes carry no labels; reach each one from the content it holds.
const treePane = () => screen.getByTestId("trace-tree").parentElement;
const graphPane = () =>
  screen.getByText("Agent Graph").parentElement.parentElement;

describe("trace left panel agent graph split", () => {
  it("shows the agent graph at half height when it is turned on after mounting hidden", () => {
    // A saved "Show agent graph = off" default mounts the panel hidden; the
    // Display switch, Reset and saved views then turn it on without a remount.
    const { rerender } = render(panel(false));
    expect(screen.queryByText("Agent Graph")).not.toBeInTheDocument();
    expect(treePane()).toHaveStyle({ height: "100%" });

    rerender(panel(true));
    expect(screen.getByTestId("agent-graph")).toBeInTheDocument();
    expect(treePane()).toHaveStyle({ height: "50%" });
    expect(graphPane()).toHaveStyle({ height: "50%" });
  });

  it("opens with the tree and the agent graph sharing the panel", () => {
    render(panel(true));
    expect(treePane()).toHaveStyle({ height: "50%" });
    expect(graphPane()).toHaveStyle({ height: "50%" });
  });

  it("gives the tree the full panel while the graph is off and restores the split", () => {
    const { rerender } = render(panel(true));

    rerender(panel(false));
    expect(screen.queryByText("Agent Graph")).not.toBeInTheDocument();
    expect(treePane()).toHaveStyle({ height: "100%" });

    rerender(panel(true));
    expect(treePane()).toHaveStyle({ height: "50%" });
    expect(graphPane()).toHaveStyle({ height: "50%" });
  });
});
