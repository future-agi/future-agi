import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import GraphView from "../GraphView";

const mocks = vi.hoisted(() => ({
  flow: vi.fn(() => null),
  dialog: vi.fn(() => null),
  permission: { isReadOnly: false },
  workflow: { isRunning: false },
  state: { nodes: [], edges: [], setGraphData: vi.fn() },
}));
vi.mock("@xyflow/react", () => ({
  ReactFlow: mocks.flow,
  Controls: () => null,
  ConnectionLineType: { SmoothStep: "smoothstep" },
  useReactFlow: () => ({ screenToFlowPosition: vi.fn() }),
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
vi.mock("../../store", () => ({
  useAgentPlaygroundStore: { getState: () => mocks.state },
  useAgentPlaygroundStoreShallow: (selector) => selector(mocks.state),
  useWorkflowRunStoreShallow: (selector) => selector(mocks.workflow),
}));
vi.mock("../../hooks/useCanEditAgent", () => ({ default: () => mocks.permission }));
vi.mock("../saveDraftContext", () => ({
  useSaveDraftContext: () => ({ ensureDraft: vi.fn() }),
}));
vi.mock("../hooks/useAddNodeOptimistic", () => ({
  default: () => ({ addNode: vi.fn() }),
}));
vi.mock("../nodes", () => ({ PromptNode: () => null, AgentNode: () => null, EvalNode: () => null }));
vi.mock("../edges", () => ({ AnimatedEdge: () => null }));
vi.mock("../../components/ConfirmationDialog", () => ({ ConfirmationDialog: mocks.dialog }));
vi.mock("src/api/agent-playground/agent-playground", () => ({
  createConnectionApi: vi.fn(), deleteConnectionApi: vi.fn(), updateNodeApi: vi.fn(), deleteNodeApi: vi.fn(),
}));

const deletion = { nodes: [{ id: "node-1", data: { label: "Saved node" } }], edges: [] };

describe("Unit: GraphView deletion permissions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.permission.isReadOnly = false;
    mocks.workflow.isRunning = false;
  });

  it.each(["read-only", "running"])("rejects node and edge deletions when %s", async (mode) => {
    mocks.permission.isReadOnly = mode === "read-only";
    mocks.workflow.isRunning = mode === "running";
    render(<GraphView />);
    const props = mocks.flow.mock.lastCall[0];
    expect(props.deleteKeyCode).toBeNull();
    await expect(props.onBeforeDelete(deletion)).resolves.toBe(false);
    await expect(props.onBeforeDelete({ nodes: [], edges: [{ id: "edge-1" }] })).resolves.toBe(false);
    expect(mocks.dialog.mock.lastCall[0].open).toBe(false);
    expect(mocks.state.setGraphData).not.toHaveBeenCalled();
  });

  it("requests confirmation for an editable node and respects cancel", async () => {
    render(<GraphView />);
    const props = mocks.flow.mock.lastCall[0];
    expect(props.deleteKeyCode).toEqual(["Backspace", "Delete"]);
    let pending;
    act(() => { pending = props.onBeforeDelete(deletion); });
    expect(mocks.dialog.mock.lastCall[0].open).toBe(true);
    act(() => mocks.dialog.mock.lastCall[0].onClose());
    await expect(pending).resolves.toBe(false);
    expect(mocks.state.setGraphData).not.toHaveBeenCalled();
  });
});
