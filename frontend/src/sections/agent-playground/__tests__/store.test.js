import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  useAgentPlaygroundStore,
  useGlobalVariablesDrawerStore,
  useWorkflowRunStore,
  useTemplateLoadingStore,
  useAgentListGridStore,
  VIEW,
} from "../store";
import { WORKFLOW_STATE } from "../utils/workflowExecution";
import { NODE_TYPES } from "../utils/constants";

// Mock notistack
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Mock logger
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

// ---------------------------------------------------------------------------
// useAgentPlaygroundStore
// ---------------------------------------------------------------------------
describe("useAgentPlaygroundStore", () => {
  beforeEach(() => {
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
  });

  it("has correct initial state", () => {
    const state = useAgentPlaygroundStore.getState();
    expect(state.nodes).toEqual([]);
    expect(state.edges).toEqual([]);
    expect(state.currentAgent).toBeNull();
    expect(state.selectedNode).toBeNull();
    expect(state.validationErrorNodeIds).toEqual([]);
    expect(state.nodeExecutionStates).toEqual({});
    expect(state.edgeExecutionStates).toEqual({});
  });

  describe("setCurrentAgent", () => {
    it("sets the current agent", () => {
      const agent = { id: "a1", name: "Test Agent" };
      useAgentPlaygroundStore.getState().setCurrentAgent(agent);
      expect(useAgentPlaygroundStore.getState().currentAgent).toEqual(agent);
    });
  });

  describe("addOptimisticNode", () => {
    it("adds a node of valid type", () => {
      useAgentPlaygroundStore
        .getState()
        .addOptimisticNode(NODE_TYPES.LLM_PROMPT);
      const { nodes } = useAgentPlaygroundStore.getState();
      expect(nodes).toHaveLength(1);
      expect(nodes[0].type).toBe(NODE_TYPES.LLM_PROMPT);
    });

    it("calculates position based on existing nodes", () => {
      useAgentPlaygroundStore
        .getState()
        .addOptimisticNode(NODE_TYPES.LLM_PROMPT);
      useAgentPlaygroundStore
        .getState()
        .addOptimisticNode(NODE_TYPES.LLM_PROMPT);
      const { nodes } = useAgentPlaygroundStore.getState();
      expect(nodes[1].position.x).toBeGreaterThan(nodes[0].position.x);
    });

    it("stores node_template_id when provided", () => {
      useAgentPlaygroundStore
        .getState()
        .addOptimisticNode(NODE_TYPES.LLM_PROMPT, null, null, "tpl-1");
      const { nodes } = useAgentPlaygroundStore.getState();
      expect(nodes[0].data.node_template_id).toBe("tpl-1");
    });

    it("derives JSON response port schema from prompt config", () => {
      const responseSchema = {
        type: "object",
        properties: { answer: { type: "string" } },
      };

      const result = useAgentPlaygroundStore
        .getState()
        .addOptimisticNode(
          NODE_TYPES.LLM_PROMPT,
          null,
          null,
          "tpl-1",
          "json_prompt",
          {
            outputFormat: "json",
            modelConfig: { responseSchema },
          },
        );

      const { nodes } = useAgentPlaygroundStore.getState();
      expect(nodes[0].data.ports[0].data_schema).toEqual(responseSchema);
      expect(result.ports[0].data_schema).toEqual(responseSchema);
    });

    it("is blocked during workflow run", () => {
      useWorkflowRunStore.setState({ isRunning: true });
      const result = useAgentPlaygroundStore
        .getState()
        .addOptimisticNode(NODE_TYPES.LLM_PROMPT);
      expect(result).toBeNull();
      expect(useAgentPlaygroundStore.getState().nodes).toEqual([]);
    });
  });

  describe("deleteNode", () => {
    beforeEach(() => {
      useAgentPlaygroundStore.setState({
        nodes: [
          {
            id: "n1",
            type: NODE_TYPES.LLM_PROMPT,
            position: { x: 0, y: 0 },
            data: {},
          },
          {
            id: "n2",
            type: NODE_TYPES.LLM_PROMPT,
            position: { x: 100, y: 0 },
            data: {},
          },
        ],
        edges: [{ id: "e1", source: "n1", target: "n2" }],
        selectedNode: {
          id: "n1",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: {},
        },
      });
    });

    it("removes the node and connected edges", () => {
      useAgentPlaygroundStore.getState().deleteNode("n1");
      const state = useAgentPlaygroundStore.getState();
      expect(state.nodes).toHaveLength(1);
      expect(state.nodes[0].id).toBe("n2");
      expect(state.edges).toHaveLength(0);
    });

    it("clears selectedNode when it is deleted", () => {
      useAgentPlaygroundStore.getState().deleteNode("n1");
      expect(useAgentPlaygroundStore.getState().selectedNode).toBeNull();
    });

    it("is blocked during workflow run", () => {
      useWorkflowRunStore.setState({ isRunning: true });
      useAgentPlaygroundStore.getState().deleteNode("n1");
      expect(useAgentPlaygroundStore.getState().nodes).toHaveLength(2);
    });
  });

  describe("onConnect", () => {
    beforeEach(() => {
      useAgentPlaygroundStore.setState({
        nodes: [
          {
            id: "n1",
            type: NODE_TYPES.LLM_PROMPT,
            position: { x: 0, y: 0 },
            data: {},
          },
          {
            id: "n2",
            type: NODE_TYPES.LLM_PROMPT,
            position: { x: 100, y: 0 },
            data: {},
          },
          {
            id: "n3",
            type: NODE_TYPES.LLM_PROMPT,
            position: { x: 200, y: 0 },
            data: {},
          },
        ],
        edges: [],
      });
    });

    it("adds a new edge", () => {
      useAgentPlaygroundStore.getState().onConnect({
        source: "n1",
        target: "n2",
        sourceHandle: "response",
        targetHandle: "input",
      });
      expect(useAgentPlaygroundStore.getState().edges).toHaveLength(1);
    });

    it("allows multiple sources to same target (many-to-one)", () => {
      useAgentPlaygroundStore.getState().onConnect({
        source: "n1",
        target: "n2",
        sourceHandle: "response",
        targetHandle: "input",
      });
      // Connect another source to same target — now allowed (node-to-node)
      useAgentPlaygroundStore.getState().onConnect({
        source: "n3",
        target: "n2",
        sourceHandle: "response",
        targetHandle: "input",
      });
      expect(useAgentPlaygroundStore.getState().edges).toHaveLength(2);
    });

    it("allows connections that form a cycle", () => {
      // n1 → n2
      useAgentPlaygroundStore.getState().onConnect({
        source: "n1",
        target: "n2",
        sourceHandle: "response",
        targetHandle: "input",
      });
      // n2 → n1 (cycle is now allowed)
      useAgentPlaygroundStore.getState().onConnect({
        source: "n2",
        target: "n1",
        sourceHandle: "response",
        targetHandle: "input",
      });
      expect(useAgentPlaygroundStore.getState().edges).toHaveLength(2);
    });
  });

  describe("updateNodeData", () => {
    it("updates node data", () => {
      useAgentPlaygroundStore.setState({
        nodes: [
          {
            id: "n1",
            type: NODE_TYPES.LLM_PROMPT,
            position: { x: 0, y: 0 },
            data: { label: "Old" },
          },
        ],
        edges: [],
      });
      useAgentPlaygroundStore.getState().updateNodeData("n1", { label: "New" });
      expect(useAgentPlaygroundStore.getState().nodes[0].data.label).toBe(
        "New",
      );
    });

    it("does not touch edges when ports change (edge cleanup is external)", () => {
      useAgentPlaygroundStore.setState({
        nodes: [
          {
            id: "n1",
            type: NODE_TYPES.LLM_PROMPT,
            position: { x: 0, y: 0 },
            data: {},
          },
          {
            id: "n2",
            type: NODE_TYPES.LLM_PROMPT,
            position: { x: 100, y: 0 },
            data: {},
          },
        ],
        edges: [
          {
            id: "e1",
            source: "n1",
            target: "n2",
            sourceHandle: "old_port",
            targetHandle: "input",
          },
        ],
      });

      // Update n1 ports — updateNodeData no longer prunes stale edges
      useAgentPlaygroundStore.getState().updateNodeData("n1", {
        config: {
          payload: {
            ports: [{ display_name: "new_port", direction: "output" }],
          },
        },
      });

      expect(useAgentPlaygroundStore.getState().edges).toHaveLength(1);
    });
  });

  describe("validation actions", () => {
    it("setValidationErrorNodeIds and clearValidationErrors", () => {
      const { setValidationErrorNodeIds, clearValidationErrors } =
        useAgentPlaygroundStore.getState();
      setValidationErrorNodeIds(["n1", "n2"]);
      expect(useAgentPlaygroundStore.getState().validationErrorNodeIds).toEqual(
        ["n1", "n2"],
      );
      clearValidationErrors();
      expect(useAgentPlaygroundStore.getState().validationErrorNodeIds).toEqual(
        [],
      );
    });

    it("clearValidationErrorNode removes single node", () => {
      useAgentPlaygroundStore
        .getState()
        .setValidationErrorNodeIds(["n1", "n2"]);
      useAgentPlaygroundStore.getState().clearValidationErrorNode("n1");
      expect(useAgentPlaygroundStore.getState().validationErrorNodeIds).toEqual(
        ["n2"],
      );
    });
  });

  describe("execution state actions", () => {
    it("set and clear node execution states", () => {
      useAgentPlaygroundStore.getState().setNodeExecutionState("n1", "running");
      expect(useAgentPlaygroundStore.getState().nodeExecutionStates).toEqual({
        n1: "running",
      });
      useAgentPlaygroundStore.getState().clearAllExecutionStates();
      expect(useAgentPlaygroundStore.getState().nodeExecutionStates).toEqual(
        {},
      );
    });

    it("set and clear edge execution states", () => {
      useAgentPlaygroundStore.getState().setEdgeExecutionState("e1", "active");
      expect(useAgentPlaygroundStore.getState().edgeExecutionStates).toEqual({
        e1: "active",
      });
      useAgentPlaygroundStore.getState().clearAllExecutionStates();
      expect(useAgentPlaygroundStore.getState().edgeExecutionStates).toEqual(
        {},
      );
    });
  });

  describe("draft confirm dialog", () => {
    it("opens and closes", () => {
      const cb = vi.fn();
      useAgentPlaygroundStore.getState().openDraftConfirmDialog(cb, "msg");
      const state = useAgentPlaygroundStore.getState().draftConfirmDialog;
      expect(state.open).toBe(true);
      expect(state.callback).toBe(cb);
      expect(state.message).toBe("msg");

      useAgentPlaygroundStore.getState().closeDraftConfirmDialog();
      const closed = useAgentPlaygroundStore.getState().draftConfirmDialog;
      expect(closed.open).toBe(false);
      expect(closed.callback).toBeNull();
    });

    it("confirmDraftDialog calls callback and closes", () => {
      const cb = vi.fn();
      useAgentPlaygroundStore.getState().openDraftConfirmDialog(cb);
      useAgentPlaygroundStore.getState().confirmDraftDialog();
      expect(cb).toHaveBeenCalledOnce();
      expect(useAgentPlaygroundStore.getState().draftConfirmDialog.open).toBe(
        false,
      );
    });
  });

  describe("loadVersion", () => {
    it("parses API data into XYFlow state", () => {
      const apiData = {
        nodes: [
          {
            id: "n1",
            type: "atomic",
            name: "Prompt 1",
            config: { model: "gpt-4", messages: [] },
            position: { x: 50, y: 100 },
            ports: [],
          },
        ],
        edges: [],
      };
      useAgentPlaygroundStore.getState().loadVersion(apiData);
      const state = useAgentPlaygroundStore.getState();
      expect(state.nodes).toHaveLength(1);
      expect(state.nodes[0].type).toBe(NODE_TYPES.LLM_PROMPT);
    });

    it("clears stale nodes when response has no nodes", () => {
      useAgentPlaygroundStore.setState({
        nodes: [
          {
            id: "existing",
            type: NODE_TYPES.LLM_PROMPT,
            position: { x: 0, y: 0 },
            data: {},
          },
        ],
      });
      useAgentPlaygroundStore.getState().loadVersion({ nodes: [] });
      const state = useAgentPlaygroundStore.getState();
      expect(state.nodes).toHaveLength(0);
      expect(state.edges).toHaveLength(0);
      expect(state.selectedNode).toBeNull();
      expect(state.isGraphReady).toBe(true);
    });
  });

  describe("reset", () => {
    it("restores initial state", () => {
      useAgentPlaygroundStore.setState({
        nodes: [{ id: "n1" }],
        currentAgent: { id: "a1" },
      });
      useAgentPlaygroundStore.getState().reset();
      const state = useAgentPlaygroundStore.getState();
      expect(state.nodes).toEqual([]);
      expect(state.currentAgent).toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// useGlobalVariablesDrawerStore
// ---------------------------------------------------------------------------
describe("useGlobalVariablesDrawerStore", () => {
  beforeEach(() => {
    useGlobalVariablesDrawerStore.getState().reset();
  });

  it("has correct initial state", () => {
    const state = useGlobalVariablesDrawerStore.getState();
    expect(state.open).toBe(false);
    expect(state.pendingRun).toBe(false);
    expect(state.currentView).toBe(VIEW.MANUAL_FORM);
    expect(state.uploadedJson).toBeNull();
    expect(state.importDatasetDrawerOpen).toBe(false);
  });

  it("setOpen toggles open state", () => {
    useGlobalVariablesDrawerStore.getState().setOpen(true);
    expect(useGlobalVariablesDrawerStore.getState().open).toBe(true);
  });

  it("setPendingRun toggles pending run", () => {
    useGlobalVariablesDrawerStore.getState().setPendingRun(true);
    expect(useGlobalVariablesDrawerStore.getState().pendingRun).toBe(true);
  });

  it("setCurrentView changes view", () => {
    useGlobalVariablesDrawerStore.getState().setCurrentView(VIEW.ACTIONS);
    expect(useGlobalVariablesDrawerStore.getState().currentView).toBe(
      VIEW.ACTIONS,
    );
  });

  describe("globalVariables CRUD", () => {
    it("updateGlobalVariables updates a single key", () => {
      useGlobalVariablesDrawerStore
        .getState()
        .updateGlobalVariables("name", "Alice");
      expect(
        useGlobalVariablesDrawerStore.getState().globalVariables.name,
      ).toBe("Alice");
    });

    it("setGlobalVariables replaces all variables", () => {
      useGlobalVariablesDrawerStore
        .getState()
        .setGlobalVariables({ foo: "bar" });
      const vars = useGlobalVariablesDrawerStore.getState().globalVariables;
      expect(vars).toEqual({ foo: "bar" });
      expect(vars.name).toBeUndefined();
    });

    it("addGlobalVariableKey adds empty key", () => {
      useGlobalVariablesDrawerStore.getState().addGlobalVariableKey("city");
      expect(
        useGlobalVariablesDrawerStore.getState().globalVariables.city,
      ).toBe("");
    });

    it("deleteGlobalVariables removes key", () => {
      useGlobalVariablesDrawerStore.getState().deleteGlobalVariables("name");
      expect(
        useGlobalVariablesDrawerStore.getState().globalVariables.name,
      ).toBeUndefined();
    });
  });

  it("setUploadedJson stores json and filename", () => {
    useGlobalVariablesDrawerStore
      .getState()
      .setUploadedJson({ key: "val" }, "test.json");
    const state = useGlobalVariablesDrawerStore.getState();
    expect(state.uploadedJson).toEqual({ key: "val" });
    expect(state.uploadedFileName).toBe("test.json");
  });

  it("reset restores initial state (pendingRun false)", () => {
    useGlobalVariablesDrawerStore.setState({
      open: true,
      pendingRun: true,
      currentView: VIEW.ACTIONS,
    });
    useGlobalVariablesDrawerStore.getState().reset();
    const state = useGlobalVariablesDrawerStore.getState();
    expect(state.open).toBe(false);
    expect(state.pendingRun).toBe(false);
    expect(state.currentView).toBe(VIEW.MANUAL_FORM);
  });
});

// ---------------------------------------------------------------------------
// useWorkflowRunStore
// ---------------------------------------------------------------------------
describe("useWorkflowRunStore", () => {
  beforeEach(() => {
    useWorkflowRunStore.getState().reset();
  });

  it("has correct initial state", () => {
    const state = useWorkflowRunStore.getState();
    expect(state.workflowState).toBe(WORKFLOW_STATE.IDLE);
    expect(state.isRunning).toBe(false);
    expect(state.hasRun).toBe(false);
    expect(state.runResults).toEqual({});
  });

  it("setWorkflowState derives isRunning from RUNNING", () => {
    useWorkflowRunStore.getState().setWorkflowState(WORKFLOW_STATE.RUNNING);
    expect(useWorkflowRunStore.getState().isRunning).toBe(true);

    useWorkflowRunStore.getState().setWorkflowState(WORKFLOW_STATE.COMPLETED);
    expect(useWorkflowRunStore.getState().isRunning).toBe(false);
  });

  it("startRun sets isRunning and clears results", () => {
    useWorkflowRunStore.setState({ runResults: { n1: {} } });
    useWorkflowRunStore.getState().startRun();
    const state = useWorkflowRunStore.getState();
    expect(state.isRunning).toBe(true);
    expect(state.runError).toBeNull();
    expect(state.runResults).toEqual({});
  });

  it("completeRun sets results and selectedOutputNodeId", () => {
    const results = { n1: { rows: [] } };
    useWorkflowRunStore.getState().completeRun(results, "n1");
    const state = useWorkflowRunStore.getState();
    expect(state.isRunning).toBe(false);
    expect(state.hasRun).toBe(true);
    expect(state.showOutput).toBe(true);
    expect(state.runResults).toEqual(results);
    expect(state.selectedOutputNodeId).toBe("n1");
    expect(state.workflowState).toBe(WORKFLOW_STATE.COMPLETED);
  });

  it("failRun sets error", () => {
    useWorkflowRunStore.getState().failRun("Something went wrong");
    const state = useWorkflowRunStore.getState();
    expect(state.isRunning).toBe(false);
    expect(state.runError).toBe("Something went wrong");
    expect(state.workflowState).toBe(WORKFLOW_STATE.ERROR);
  });

  it("failRun with results sets showOutput and runResults", () => {
    const results = { n1: { rows: [] } };
    useWorkflowRunStore.getState().failRun("Error occurred", results);
    const state = useWorkflowRunStore.getState();
    expect(state.isRunning).toBe(false);
    expect(state.runError).toBe("Error occurred");
    expect(state.workflowState).toBe(WORKFLOW_STATE.ERROR);
    expect(state.hasRun).toBe(true);
    expect(state.showOutput).toBe(true);
    expect(state.runResults).toEqual(results);
  });

  it("setRunResults merges results", () => {
    useWorkflowRunStore.setState({ runResults: { n1: { rows: [1] } } });
    useWorkflowRunStore.getState().setRunResults("n2", { rows: [2] });
    const results = useWorkflowRunStore.getState().runResults;
    expect(results.n1).toEqual({ rows: [1] });
    expect(results.n2).toEqual({ rows: [2] });
  });

  it("reset restores initial state", () => {
    useWorkflowRunStore.setState({
      isRunning: true,
      hasRun: true,
      workflowState: WORKFLOW_STATE.RUNNING,
    });
    useWorkflowRunStore.getState().reset();
    const state = useWorkflowRunStore.getState();
    expect(state.isRunning).toBe(false);
    expect(state.hasRun).toBe(false);
    expect(state.workflowState).toBe(WORKFLOW_STATE.IDLE);
  });
});

// ---------------------------------------------------------------------------
// useTemplateLoadingStore
// ---------------------------------------------------------------------------
describe("useTemplateLoadingStore", () => {
  beforeEach(() => {
    useTemplateLoadingStore.getState().reset();
  });

  it("startLoadingTemplate returns AbortController", () => {
    const controller = useTemplateLoadingStore
      .getState()
      .startLoadingTemplate("tpl-1", "My Template");
    expect(controller).toBeInstanceOf(AbortController);
    const state = useTemplateLoadingStore.getState();
    expect(state.isLoadingTemplate).toBe(true);
    expect(state.loadingTemplateId).toBe("tpl-1");
    expect(state.loadingTemplateName).toBe("My Template");
  });

  it("updateProgress sets progress and message", () => {
    useTemplateLoadingStore.getState().updateLoadingProgress(50, "Halfway");
    const state = useTemplateLoadingStore.getState();
    expect(state.loadingProgress).toBe(50);
    expect(state.loadingMessage).toBe("Halfway");
  });

  it("completeLoadingTemplate clears loading state", () => {
    useTemplateLoadingStore.getState().startLoadingTemplate("tpl-1", "Test");
    useTemplateLoadingStore.getState().completeLoadingTemplate();
    const state = useTemplateLoadingStore.getState();
    expect(state.isLoadingTemplate).toBe(false);
    expect(state.loadingProgress).toBe(100);
    expect(state.loadingTemplateId).toBeNull();
  });

  it("cancelLoadingTemplate calls abort on controller", () => {
    const controller = useTemplateLoadingStore
      .getState()
      .startLoadingTemplate("tpl-1", "Test");
    const abortSpy = vi.spyOn(controller, "abort");
    useTemplateLoadingStore.getState().cancelLoadingTemplate();
    expect(abortSpy).toHaveBeenCalled();
    expect(useTemplateLoadingStore.getState().isLoadingTemplate).toBe(false);
  });

  it("reset aborts controller", () => {
    const controller = useTemplateLoadingStore
      .getState()
      .startLoadingTemplate("tpl-1", "Test");
    const abortSpy = vi.spyOn(controller, "abort");
    useTemplateLoadingStore.getState().reset();
    expect(abortSpy).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// useAgentListGridStore
// ---------------------------------------------------------------------------
describe("useAgentListGridStore", () => {
  beforeEach(() => {
    useAgentListGridStore.getState().reset();
  });

  it("has correct initial state", () => {
    const state = useAgentListGridStore.getState();
    expect(state.toggledNodes).toEqual([]);
    expect(state.selectAll).toBe(false);
    expect(state.totalRowCount).toBe(0);
    expect(state.gridApi).toBeNull();
  });

  it("setToggledNodes updates toggled nodes", () => {
    useAgentListGridStore.getState().setToggledNodes(["a", "b"]);
    expect(useAgentListGridStore.getState().toggledNodes).toEqual(["a", "b"]);
  });

  it("setSelectAll updates select all", () => {
    useAgentListGridStore.getState().setSelectAll(true);
    expect(useAgentListGridStore.getState().selectAll).toBe(true);
  });

  it("setTotalRowCount updates count", () => {
    useAgentListGridStore.getState().setTotalRowCount(42);
    expect(useAgentListGridStore.getState().totalRowCount).toBe(42);
  });

  it("setGridApi stores the api instance", () => {
    const fakeApi = { refreshServerSide: vi.fn() };
    useAgentListGridStore.getState().setGridApi(fakeApi);
    expect(useAgentListGridStore.getState().gridApi).toBe(fakeApi);
  });

  it("reset restores initial state", () => {
    useAgentListGridStore.setState({
      toggledNodes: ["a"],
      selectAll: true,
      totalRowCount: 10,
    });
    useAgentListGridStore.getState().reset();
    const state = useAgentListGridStore.getState();
    expect(state.toggledNodes).toEqual([]);
    expect(state.selectAll).toBe(false);
    expect(state.totalRowCount).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------
describe("useAgentPlaygroundStore – edge cases", () => {
  beforeEach(() => {
    useAgentPlaygroundStore.getState().reset();
    useWorkflowRunStore.getState().reset();
  });

  it("addOptimisticNode generates unique IDs for same type added twice", () => {
    useAgentPlaygroundStore.getState().addOptimisticNode(NODE_TYPES.LLM_PROMPT);
    useAgentPlaygroundStore.getState().addOptimisticNode(NODE_TYPES.LLM_PROMPT);
    const { nodes } = useAgentPlaygroundStore.getState();
    expect(nodes).toHaveLength(2);
    expect(nodes[0].id).not.toBe(nodes[1].id);
  });

  it("addOptimisticNode avoids label collision with API-loaded nodes", () => {
    // Simulate nodes loaded from API with UUID ids but sequential labels
    useAgentPlaygroundStore.setState({
      nodes: [
        {
          id: "uuid-aaa-111",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: { label: "llm_prompt_node_1" },
        },
        {
          id: "uuid-bbb-222",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 100, y: 0 },
          data: { label: "llm_prompt_node_2" },
        },
      ],
      edges: [],
    });

    // Add a new node — label should not collide with existing labels
    useAgentPlaygroundStore.getState().addOptimisticNode(NODE_TYPES.LLM_PROMPT);
    const { nodes } = useAgentPlaygroundStore.getState();
    expect(nodes).toHaveLength(3);
    // The new node gets a unique label (not reusing existing ones)
    const labels = nodes.map((n) => n.data.label);
    const uniqueLabels = new Set(labels);
    expect(uniqueLabels.size).toBe(3);
  });

  it("addOptimisticNode generates unique ID after a node is deleted (no duplicate IDs)", () => {
    useAgentPlaygroundStore.getState().addOptimisticNode(NODE_TYPES.LLM_PROMPT);
    useAgentPlaygroundStore.getState().addOptimisticNode(NODE_TYPES.LLM_PROMPT);
    useAgentPlaygroundStore.getState().addOptimisticNode(NODE_TYPES.LLM_PROMPT);

    const { nodes: before } = useAgentPlaygroundStore.getState();
    expect(before).toHaveLength(3);

    // Delete the middle node
    useAgentPlaygroundStore.getState().deleteNode(before[1].id);
    expect(useAgentPlaygroundStore.getState().nodes).toHaveLength(2);

    // Add a new node — UUID IDs are always unique
    useAgentPlaygroundStore.getState().addOptimisticNode(NODE_TYPES.LLM_PROMPT);
    const { nodes: after } = useAgentPlaygroundStore.getState();
    expect(after).toHaveLength(3);

    const ids = after.map((n) => n.id);
    const uniqueIds = new Set(ids);
    expect(uniqueIds.size).toBe(3);
  });

  it("onConnect allows self-loop (source === target)", () => {
    useAgentPlaygroundStore.setState({
      nodes: [
        {
          id: "n1",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: {},
        },
      ],
      edges: [],
    });
    useAgentPlaygroundStore.getState().onConnect({
      source: "n1",
      target: "n1",
      sourceHandle: "response",
      targetHandle: "input",
    });
    expect(useAgentPlaygroundStore.getState().edges).toHaveLength(1);
  });

  it("updateNodeData is no-op for non-existent nodeId", () => {
    useAgentPlaygroundStore.setState({
      nodes: [
        {
          id: "n1",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: { label: "A" },
        },
      ],
      edges: [],
    });
    useAgentPlaygroundStore
      .getState()
      .updateNodeData("non-existent", { label: "B" });
    expect(useAgentPlaygroundStore.getState().nodes[0].data.label).toBe("A");
  });

  it("updateNodeData without ports does not touch edges", () => {
    useAgentPlaygroundStore.setState({
      nodes: [
        {
          id: "n1",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: {},
        },
        {
          id: "n2",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 100, y: 0 },
          data: {},
        },
      ],
      edges: [
        {
          id: "e1",
          source: "n1",
          target: "n2",
          sourceHandle: "out",
          targetHandle: "in",
        },
      ],
    });
    // Update without config.payload.ports — edges should remain
    useAgentPlaygroundStore
      .getState()
      .updateNodeData("n1", { label: "Updated" });
    expect(useAgentPlaygroundStore.getState().edges).toHaveLength(1);
  });

  it("deleteNode during workflow run is blocked — nodes unchanged", () => {
    useAgentPlaygroundStore.setState({
      nodes: [
        {
          id: "n1",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: {},
        },
      ],
      edges: [],
    });
    useWorkflowRunStore.setState({ isRunning: true });
    useAgentPlaygroundStore.getState().deleteNode("n1");
    expect(useAgentPlaygroundStore.getState().nodes).toHaveLength(1);
  });

  it("setSelectedNode during workflow run is blocked", () => {
    useWorkflowRunStore.setState({ isRunning: true });
    useAgentPlaygroundStore.getState().setSelectedNode({ id: "n1" });
    expect(useAgentPlaygroundStore.getState().selectedNode).toBeNull();
  });

  it("onEdgesChange allows removal when deletable is undefined", () => {
    useAgentPlaygroundStore.setState({
      nodes: [
        {
          id: "n1",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: {},
        },
        {
          id: "n2",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 100, y: 0 },
          data: {},
        },
      ],
      edges: [{ id: "e1", source: "n1", target: "n2" }],
    });
    // deletable is undefined (not explicitly false) — should allow removal
    useAgentPlaygroundStore
      .getState()
      .onEdgesChange([{ type: "remove", id: "e1" }]);
    expect(useAgentPlaygroundStore.getState().edges).toHaveLength(0);
  });

  it("onEdgesChange blocks removal when deletable is false and both nodes exist", () => {
    useAgentPlaygroundStore.setState({
      nodes: [
        {
          id: "n1",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: {},
        },
        {
          id: "n2",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 100, y: 0 },
          data: {},
        },
      ],
      edges: [{ id: "e1", source: "n1", target: "n2", deletable: false }],
    });
    useAgentPlaygroundStore
      .getState()
      .onEdgesChange([{ type: "remove", id: "e1" }]);
    // Edge should NOT be removed because both nodes exist and edge is non-deletable
    expect(useAgentPlaygroundStore.getState().edges).toHaveLength(1);
  });

  it("action sequence: addOptimisticNode → connect → deleteNode cleans up edges", () => {
    useAgentPlaygroundStore.getState().addOptimisticNode(NODE_TYPES.LLM_PROMPT);
    useAgentPlaygroundStore.getState().addOptimisticNode(NODE_TYPES.LLM_PROMPT);
    const { nodes } = useAgentPlaygroundStore.getState();

    useAgentPlaygroundStore.getState().onConnect({
      source: nodes[0].id,
      target: nodes[1].id,
      sourceHandle: "response",
      targetHandle: "input",
    });
    expect(useAgentPlaygroundStore.getState().edges).toHaveLength(1);

    useAgentPlaygroundStore.getState().deleteNode(nodes[0].id);
    expect(useAgentPlaygroundStore.getState().nodes).toHaveLength(1);
    expect(useAgentPlaygroundStore.getState().edges).toHaveLength(0);
  });

  it("updateVersion is no-op when currentAgent is null", () => {
    useAgentPlaygroundStore.setState({ currentAgent: null });
    useAgentPlaygroundStore.getState().updateVersion("v1", 1);
    expect(useAgentPlaygroundStore.getState().currentAgent).toBeNull();
  });

  it("onNodesChange blocks removal during workflow run", () => {
    useAgentPlaygroundStore.setState({
      nodes: [
        {
          id: "n1",
          type: NODE_TYPES.LLM_PROMPT,
          position: { x: 0, y: 0 },
          data: {},
        },
      ],
      edges: [],
    });
    useWorkflowRunStore.setState({ isRunning: true });
    useAgentPlaygroundStore
      .getState()
      .onNodesChange([{ type: "remove", id: "n1" }]);
    expect(useAgentPlaygroundStore.getState().nodes).toHaveLength(1);
  });
});
