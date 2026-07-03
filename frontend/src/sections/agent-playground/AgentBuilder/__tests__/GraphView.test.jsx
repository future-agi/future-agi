/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import GraphView from "../GraphView";
import { useAgentPlaygroundStore, useWorkflowRunStore } from "../../store";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const reactFlowMocks = vi.hoisted(() => ({
  props: null,
  screenToFlowPosition: vi.fn((pos) => pos),
}));

vi.mock("@xyflow/react", () => ({
  ReactFlow: (props) => {
    reactFlowMocks.props = props;
    return <div data-testid="react-flow">{props.children}</div>;
  },
  Controls: () => <div data-testid="controls" />,
  ConnectionLineType: { SmoothStep: "smoothstep" },
  useReactFlow: () => ({
    screenToFlowPosition: reactFlowMocks.screenToFlowPosition,
  }),
  ReactFlowProvider: ({ children }) => <div>{children}</div>,
  addEdge: (edge, edges) => [...edges, edge],
  applyNodeChanges: (changes, nodes) =>
    nodes.filter(
      (node) => !changes.some((c) => c.type === "remove" && c.id === node.id),
    ),
  applyEdgeChanges: (changes, edges) =>
    edges.filter(
      (edge) => !changes.some((c) => c.type === "remove" && c.id === edge.id),
    ),
}));

const draftMocks = vi.hoisted(() => ({
  ensureDraft: vi.fn(),
}));
vi.mock("../saveDraftContext", () => ({
  useSaveDraftContext: () => ({ ensureDraft: draftMocks.ensureDraft }),
}));

const addNodeMocks = vi.hoisted(() => ({
  addNode: vi.fn(),
}));
vi.mock("../hooks/useAddNodeOptimistic", () => ({
  default: () => ({ addNode: addNodeMocks.addNode }),
}));

const queryMocks = vi.hoisted(() => ({
  invalidateQueries: vi.fn(),
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => queryMocks,
}));

const apiMocks = vi.hoisted(() => ({
  createConnectionApi: vi.fn(),
  deleteConnectionApi: vi.fn(),
  updateNodeApi: vi.fn(),
  deleteNodeApi: vi.fn(),
}));
vi.mock("src/api/agent-playground/agent-playground", () => ({
  createConnectionApi: apiMocks.createConnectionApi,
  deleteConnectionApi: apiMocks.deleteConnectionApi,
  updateNodeApi: apiMocks.updateNodeApi,
  deleteNodeApi: apiMocks.deleteNodeApi,
}));

const notificationMocks = vi.hoisted(() => ({
  enqueueSnackbar: vi.fn(),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: notificationMocks.enqueueSnackbar,
}));

vi.mock("../nodes", () => ({
  PromptNode: () => <div />,
  AgentNode: () => <div />,
  EvalNode: () => <div />,
}));

vi.mock("../edges", () => ({
  AnimatedEdge: () => <div />,
}));

vi.mock("../../components/ConfirmationDialog", () => ({
  ConfirmationDialog: ({ open, onClose, onConfirm, title }) =>
    open ? (
      <div data-testid="confirm-dialog" role="dialog" aria-label={title}>
        <button data-testid="confirm-btn" onClick={onConfirm}>
          Confirm
        </button>
        <button data-testid="cancel-btn" onClick={onClose}>
          Cancel
        </button>
      </div>
    ) : null,
}));

vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn() },
}));

const node = {
  id: "n1",
  type: "agent",
  position: { x: 0, y: 0 },
  data: { label: "Agent node" },
};

const edge = { id: "e1", source: "n1", target: "n2" };

function setGraphState(overrides = {}) {
  useAgentPlaygroundStore.setState({
    nodes: [node],
    edges: [edge],
    currentAgent: { id: "graph-1", version_id: "version-1", is_draft: true },
    nodeExecutionStates: {},
    ...overrides,
  });
}

function renderGraphView() {
  render(<GraphView />);
  expect(reactFlowMocks.props).toBeTruthy();
  return reactFlowMocks.props;
}

// ---------------------------------------------------------------------------
// Tests: GraphView callback logic
// ---------------------------------------------------------------------------
describe("GraphView – callback logic", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    reactFlowMocks.props = null;
    reactFlowMocks.screenToFlowPosition.mockImplementation((pos) => pos);
    draftMocks.ensureDraft.mockResolvedValue("existing");
    addNodeMocks.addNode.mockResolvedValue(true);
    apiMocks.createConnectionApi.mockResolvedValue({});
    apiMocks.deleteConnectionApi.mockResolvedValue({});
    apiMocks.updateNodeApi.mockResolvedValue({});
    apiMocks.deleteNodeApi.mockResolvedValue({});
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
    setGraphState();
  });

  describe("onBeforeDelete logic", () => {
    it("resolves immediately for empty deletions", async () => {
      const props = renderGraphView();

      const result = await props.onBeforeDelete({ nodes: [], edges: [] });

      expect(result).toBe(true);
    });

    it("opens confirmation for non-running node deletions", async () => {
      const user = userEvent.setup();
      const props = renderGraphView();

      let deletePromise;
      await act(async () => {
        deletePromise = props.onBeforeDelete({ nodes: [node], edges: [] });
      });

      expect(screen.getByTestId("confirm-dialog")).toBeInTheDocument();

      await user.click(screen.getByTestId("confirm-btn"));
      await expect(deletePromise).resolves.toBe(true);
      expect(draftMocks.ensureDraft).toHaveBeenCalledWith({
        skipDirtyCheck: true,
      });
    });

    it("blocks ReactFlow deletion before confirmation when a target node is running", async () => {
      setGraphState({ nodeExecutionStates: { n1: "running" } });
      const props = renderGraphView();

      const result = await props.onBeforeDelete({ nodes: [node], edges: [] });

      expect(result).toBe(false);
      expect(screen.queryByTestId("confirm-dialog")).not.toBeInTheDocument();
      expect(draftMocks.ensureDraft).not.toHaveBeenCalled();
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
      expect(notificationMocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Wait for this node to finish running before deleting it",
        { variant: "info" },
      );
    });

    it("re-checks node running state before confirming a pending delete", async () => {
      const user = userEvent.setup();
      const props = renderGraphView();

      let deletePromise;
      await act(async () => {
        deletePromise = props.onBeforeDelete({ nodes: [node], edges: [] });
      });
      expect(screen.getByTestId("confirm-dialog")).toBeInTheDocument();

      useAgentPlaygroundStore.setState({
        nodeExecutionStates: { n1: "running" },
      });

      await user.click(screen.getByTestId("confirm-btn"));

      await expect(deletePromise).resolves.toBe(false);
      expect(draftMocks.ensureDraft).not.toHaveBeenCalled();
      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
      expect(notificationMocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Wait for this node to finish running before deleting it",
        { variant: "info" },
      );
    });
  });

  describe("handlePostDelete logic", () => {
    it("does not persist a stale ReactFlow delete for a running node", async () => {
      setGraphState({ nodeExecutionStates: { n1: "running" } });
      const props = renderGraphView();

      await props.onDelete({ nodes: [node], edges: [] });

      expect(apiMocks.deleteNodeApi).not.toHaveBeenCalled();
      expect(apiMocks.deleteConnectionApi).not.toHaveBeenCalled();
      expect(notificationMocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Wait for this node to finish running before deleting it",
        { variant: "info" },
      );
    });
  });

  describe("onDrop", () => {
    it("extracts node type and adds node at converted position", () => {
      reactFlowMocks.screenToFlowPosition.mockReturnValue({ x: 100, y: 200 });
      const props = renderGraphView();
      const event = {
        preventDefault: vi.fn(),
        clientX: 170,
        clientY: 220,
        dataTransfer: {
          getData: vi.fn((key) => {
            if (key === "application/reactflow") return "llm_prompt";
            if (key === "application/node-template-id") return "tpl-1";
            return "";
          }),
        },
      };

      props.onDrop(event);

      expect(event.preventDefault).toHaveBeenCalled();
      expect(addNodeMocks.addNode).toHaveBeenCalledWith({
        type: "llm_prompt",
        position: { x: 100, y: 200 },
        node_template_id: "tpl-1",
      });
    });

    it("does nothing when type is empty", () => {
      const props = renderGraphView();
      const event = {
        preventDefault: vi.fn(),
        dataTransfer: {
          getData: vi.fn(() => ""),
        },
      };

      props.onDrop(event);

      expect(event.preventDefault).toHaveBeenCalled();
      expect(addNodeMocks.addNode).not.toHaveBeenCalled();
    });
  });

  describe("connection tracking", () => {
    it("sets and clears connection state", () => {
      const props = renderGraphView();

      props.onConnectStart(null, { nodeId: "n1" });
      expect(useAgentPlaygroundStore.getState().isConnecting).toBe(true);
      expect(useAgentPlaygroundStore.getState().connectingFromNodeId).toBe(
        "n1",
      );

      props.onConnectEnd();
      expect(useAgentPlaygroundStore.getState().isConnecting).toBe(false);
      expect(
        useAgentPlaygroundStore.getState().connectingFromNodeId,
      ).toBeNull();
    });
  });
});
