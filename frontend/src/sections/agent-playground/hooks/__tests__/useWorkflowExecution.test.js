import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import useWorkflowExecution from "../useWorkflowExecution";
import {
  useAgentPlaygroundStore,
  useWorkflowRunStore,
  useGlobalVariablesDrawerStore,
} from "../../store";
import { WORKFLOW_STATE } from "../../utils/workflowExecution";
import {
  createPromptNode,
  createEdge,
  createUnconfiguredNode,
  createCyclicGraph,
} from "../../utils/__tests__/fixtures";

// Mock react-router-dom
vi.mock("react-router-dom", () => ({
  useParams: () => ({ agentId: "graph-123" }),
  useSearchParams: () => [new URLSearchParams({ version: "v1" })],
}));

// Mock snackbar
const mockEnqueueSnackbar = vi.fn();
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

// Mock API module
const mockFetchGraphDataset = vi.fn();
const mockExecuteDataset = vi.fn();
const mockActivateVersion = vi.fn();
vi.mock("../../../../api/agent-playground/agent-playground", () => ({
  fetchGraphDataset: (...args) => mockFetchGraphDataset(...args),
  useExecuteDataset: () => ({ mutateAsync: mockExecuteDataset }),
}));
vi.mock("../../../../api/agent-playground/versions", () => ({
  useActivateVersion: () => ({ mutateAsync: mockActivateVersion }),
}));

// Mock logger
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

describe("useWorkflowExecution", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
    useGlobalVariablesDrawerStore.getState().reset();
  });

  function setupNodes(nodes, edges = []) {
    useAgentPlaygroundStore.setState({ nodes, edges });
  }

  describe("runWorkflow", () => {
    it("clears validation and execution states", async () => {
      const nodes = [createPromptNode("n1")];
      setupNodes(nodes);
      mockFetchGraphDataset.mockResolvedValue({
        rows: [{ cells: [{ value: "ok" }] }],
      });
      mockExecuteDataset.mockResolvedValue({
        data: { result: { execution_ids: ["exec-1"] } },
      });

      const { result } = renderHook(() => useWorkflowExecution());
      await act(async () => {
        await result.current.runWorkflow();
      });

      // Validation errors should be cleared (store is reset in beforeEach,
      // and runWorkflow calls clearValidationErrors)
      expect(useAgentPlaygroundStore.getState().validationErrorNodeIds).toEqual(
        [],
      );
    });

    it("shows cycle error message from validateGraphForSave", async () => {
      const { nodes, edges } = createCyclicGraph();
      setupNodes(nodes, edges);

      const { result } = renderHook(() => useWorkflowExecution());
      await act(async () => {
        await result.current.runWorkflow();
      });

      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        expect.stringContaining("cycle"),
        expect.objectContaining({ variant: "error" }),
      );
    });

    it("shows 'Node not configured' for single invalid node", async () => {
      const nodes = [createPromptNode("n1"), createUnconfiguredNode("n2")];
      const edges = [createEdge("n1", "n2")];
      setupNodes(nodes, edges);

      const { result } = renderHook(() => useWorkflowExecution());
      await act(async () => {
        await result.current.runWorkflow();
      });

      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "Node not configured",
        expect.objectContaining({ variant: "error" }),
      );
    });

    it("shows 'N nodes are not configured' for multiple invalid nodes", async () => {
      const nodes = [
        createUnconfiguredNode("n1"),
        createUnconfiguredNode("n2"),
      ];
      setupNodes(nodes);

      const { result } = renderHook(() => useWorkflowExecution());
      await act(async () => {
        await result.current.runWorkflow();
      });

      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "2 nodes are not configured",
        expect.objectContaining({ variant: "error" }),
      );
    });

    it("opens drawer and sets pendingRun when dataset has empty cells", async () => {
      const nodes = [createPromptNode("n1")];
      setupNodes(nodes);
      mockFetchGraphDataset.mockResolvedValue({
        rows: [{ cells: [{ value: "" }] }],
      });

      const { result } = renderHook(() => useWorkflowExecution());
      await act(async () => {
        await result.current.runWorkflow();
      });

      expect(useGlobalVariablesDrawerStore.getState().pendingRun).toBe(true);
      expect(useGlobalVariablesDrawerStore.getState().open).toBe(true);
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "Fill in all variables before running",
        expect.objectContaining({ variant: "warning" }),
      );
    });

    it("shows error snackbar when variable fetch fails", async () => {
      const nodes = [createPromptNode("n1")];
      setupNodes(nodes);
      mockFetchGraphDataset.mockRejectedValue(new Error("Network error"));

      const { result } = renderHook(() => useWorkflowExecution());
      await act(async () => {
        await result.current.runWorkflow();
      });

      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "Failed to validate variables",
        expect.objectContaining({ variant: "error" }),
      );
    });

    it("starts polling with executionId on success", async () => {
      const nodes = [createPromptNode("n1")];
      setupNodes(nodes);
      mockFetchGraphDataset.mockResolvedValue({
        rows: [{ cells: [{ value: "ok" }] }],
      });
      mockExecuteDataset.mockResolvedValue({
        data: { result: { execution_ids: ["exec-1"] } },
      });

      const { result } = renderHook(() => useWorkflowExecution());
      await act(async () => {
        await result.current.runWorkflow();
      });

      const runState = useWorkflowRunStore.getState();
      expect(runState.workflowState).toBe(WORKFLOW_STATE.RUNNING);
      expect(runState.executionId).toBe("exec-1");
    });

    it("sets ERROR state and calls failRun on execution failure", async () => {
      const nodes = [createPromptNode("n1")];
      setupNodes(nodes);
      mockFetchGraphDataset.mockResolvedValue({
        rows: [{ cells: [{ value: "ok" }] }],
      });
      mockExecuteDataset.mockRejectedValue({
        response: { data: { result: "Custom API error" } },
      });

      const { result } = renderHook(() => useWorkflowExecution());
      await act(async () => {
        await result.current.runWorkflow();
      });

      const runState = useWorkflowRunStore.getState();
      expect(runState.workflowState).toBe(WORKFLOW_STATE.ERROR);
      expect(runState.runError).toBe("Custom API error");
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "Custom API error",
        expect.objectContaining({ variant: "error" }),
      );
    });

    it("uses error.message as fallback error message", async () => {
      const nodes = [createPromptNode("n1")];
      setupNodes(nodes);
      mockFetchGraphDataset.mockResolvedValue({
        rows: [{ cells: [{ value: "ok" }] }],
      });
      mockExecuteDataset.mockRejectedValue(new Error("Generic failure"));

      const { result } = renderHook(() => useWorkflowExecution());
      await act(async () => {
        await result.current.runWorkflow();
      });

      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "Generic failure",
        expect.objectContaining({ variant: "error" }),
      );
    });
  });

  describe("stopWorkflow", () => {
    it("clears states, sets IDLE, shows info snackbar", async () => {
      useWorkflowRunStore.getState().setWorkflowState(WORKFLOW_STATE.RUNNING);

      const { result } = renderHook(() => useWorkflowExecution());
      act(() => {
        result.current.stopWorkflow();
      });

      expect(useWorkflowRunStore.getState().workflowState).toBe(
        WORKFLOW_STATE.IDLE,
      );
      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
        "Exited workflow. It will continue running in the background.",
        expect.objectContaining({ variant: "info" }),
      );
    });
  });

  describe("isRunning", () => {
    it("reflects RUNNING state", () => {
      useWorkflowRunStore.getState().setWorkflowState(WORKFLOW_STATE.RUNNING);
      const { result } = renderHook(() => useWorkflowExecution());
      expect(result.current.isRunning).toBe(true);
    });

    it("false when IDLE", () => {
      const { result } = renderHook(() => useWorkflowExecution());
      expect(result.current.isRunning).toBe(false);
    });
  });
});

// ---------------------------------------------------------------------------
// Edge-case tests
// ---------------------------------------------------------------------------
describe("useWorkflowExecution — edge cases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
    useGlobalVariablesDrawerStore.getState().reset();
  });

  it("stopWorkflow does not throw when no workflow has been started (executorRef is null)", () => {
    // executorRef.current starts as null; stopWorkflow guards with `if (executorRef.current)`
    const { result } = renderHook(() => useWorkflowExecution());

    expect(() => {
      act(() => {
        result.current.stopWorkflow();
      });
    }).not.toThrow();

    expect(useWorkflowRunStore.getState().workflowState).toBe(
      WORKFLOW_STATE.IDLE,
    );
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Exited workflow. It will continue running in the background.",
      expect.objectContaining({ variant: "info" }),
    );
  });

  it("handles plain string error (no .response or .message) with default fallback", async () => {
    const nodes = [createPromptNode("n1")];
    useAgentPlaygroundStore.setState({ nodes, edges: [] });
    mockFetchGraphDataset.mockResolvedValue({
      rows: [{ cells: [{ value: "ok" }] }],
    });
    // Reject with a plain string — no .response, no .message
    mockExecuteDataset.mockRejectedValue("something went wrong");

    const { result } = renderHook(() => useWorkflowExecution());
    await act(async () => {
      await result.current.runWorkflow();
    });

    const runState = useWorkflowRunStore.getState();
    expect(runState.workflowState).toBe(WORKFLOW_STATE.ERROR);
    // Falls through to the default: "Workflow execution failed"
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Workflow execution failed",
      expect.objectContaining({ variant: "error" }),
    );
  });
});
