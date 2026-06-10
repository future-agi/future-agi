import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ReactFlow,
  Controls,
  ConnectionLineType,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { PromptNode, AgentNode, EvalNode, ConditionalNode } from "./nodes";
import { AnimatedEdge } from "./edges";
import { enqueueSnackbar } from "notistack";
import {
  useAgentPlaygroundStore,
  useAgentPlaygroundStoreShallow,
  useWorkflowRunStoreShallow,
} from "../store";
import { NODE_TYPES } from "../utils/constants";
import { useSaveDraftContext } from "./saveDraftContext";
import { ConfirmationDialog } from "../components/ConfirmationDialog";
import { useQueryClient } from "@tanstack/react-query";
import {
  createConnectionApi,
  deleteConnectionApi,
  updateNodeApi,
  deleteNodeApi,
} from "src/api/agent-playground/agent-playground";
import useAddNodeOptimistic from "./hooks/useAddNodeOptimistic";
import logger from "src/utils/logger";

const nodeTypes = {
  [NODE_TYPES.LLM_PROMPT]: PromptNode,
  agent: AgentNode,
  eval: EvalNode,
  [NODE_TYPES.CONDITIONAL]: ConditionalNode,
};

const edgeTypes = {
  animated: AnimatedEdge,
};

const proOptions = { hideAttribution: true };

// Drop offset to align the node under the cursor
const DROP_OFFSET_X = 70;
const DROP_OFFSET_Y = 20;

// Zoom limits
const MIN_ZOOM = 0.3;
const MAX_ZOOM = 2;

// Initial viewport
const DEFAULT_VIEWPORT = { x: 40, y: 400, zoom: 0.8 };

export default function GraphView() {
  const { screenToFlowPosition } = useReactFlow();
  const queryClient = useQueryClient();

  const {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onConnect: storeOnConnect,
    setConnectingFromNodeId,
    setIsConnecting,
    setGraphData,
  } = useAgentPlaygroundStoreShallow((state) => ({
    nodes: state.nodes,
    edges: state.edges,
    onNodesChange: state.onNodesChange,
    onEdgesChange: state.onEdgesChange,
    onConnect: state.onConnect,
    setConnectingFromNodeId: state.setConnectingFromNodeId,
    setIsConnecting: state.setIsConnecting,
    setGraphData: state.setGraphData,
  }));
  const { ensureDraft } = useSaveDraftContext();
  const isRunning = useWorkflowRunStoreShallow((s) => s.isRunning);

  // --- Position update (debounced with rollback) ---
  // Saves the original position of each dragged node before the drag begins.
  // If the backend rejects the position update, we use these saved positions
  // to move the nodes back to where they were.
  const dragStartPositionRef = useRef({});
  // Keeps track of pending debounce timers for position save API calls.
  // When a user stops dragging, we wait 500ms before sending the update.
  // If the user drags the same nodes again within that window, the previous
  // timer is cancelled and a new one starts. Each entry is keyed by the
  // sorted IDs of the dragged nodes (e.g. "nodeA,nodeB").
  const positionDebounceRef = useRef({});

  // Clean up pending debounced position saves on unmount
  useEffect(() => {
    const ref = positionDebounceRef;
    return () => {
      Object.values(ref.current).forEach(clearTimeout);
    };
  }, []);

  const onNodeDragStart = useCallback((_event, _node, nodes) => {
    nodes.forEach((n) => {
      dragStartPositionRef.current[n.id] = { ...n.position };
    });
  }, []);

  // --- Delete confirmation ---
  const [pendingDelete, setPendingDelete] = useState(null);
  const snapshotRef = useRef(null);

  const onBeforeDelete = useCallback(
    ({ nodes: deletingNodes, edges: deletingEdges }) => {
      if (isRunning) return Promise.resolve(false);
      if (deletingNodes.length === 0 && deletingEdges.length === 0)
        return Promise.resolve(true);

      // Edge-only: skip delete confirmation — ensureDraft handles the draft dialog
      // in handlePostDelete. Capture snapshot now before ReactFlow mutates the store.
      if (deletingNodes.length === 0) {
        const { nodes: n, edges: e } = useAgentPlaygroundStore.getState();
        snapshotRef.current = { nodes: n, edges: e };
        return Promise.resolve(true);
      }

      // Node deletion: show confirmation dialog
      return new Promise((resolve) => {
        setPendingDelete({
          nodes: deletingNodes,
          edges: deletingEdges,
          resolve,
        });
      });
    },
    [isRunning],
  );

  const handleConfirmDelete = useCallback(async () => {
    const { resolve } = pendingDelete;
    const { currentAgent } = useAgentPlaygroundStore.getState();
    const isActive = !currentAgent?.is_draft;

    if (isActive) {
      // Apply deletion optimistically before ensureDraft
      const nodeIds = new Set(pendingDelete.nodes.map((n) => n.id));
      const edgeIds = new Set(pendingDelete.edges.map((e) => e.id));
      const { nodes: curNodes, edges: curEdges } =
        useAgentPlaygroundStore.getState();
      setGraphData(
        curNodes.filter((n) => !nodeIds.has(n.id)),
        curEdges.filter(
          (e) =>
            !nodeIds.has(e.source) &&
            !nodeIds.has(e.target) &&
            !edgeIds.has(e.id),
        ),
      );
    }

    snapshotRef.current = { nodes, edges };

    const draftResult = await ensureDraft({ skipDirtyCheck: true });

    if (draftResult === false) {
      if (snapshotRef.current) {
        setGraphData(snapshotRef.current.nodes, snapshotRef.current.edges);
      }
      snapshotRef.current = null;
      resolve(false);
      setPendingDelete(null);
      return;
    }

    if (draftResult === "created") {
      snapshotRef.current = null;
      resolve(false);
      setPendingDelete(null);
      return;
    }

    // Already a draft — let ReactFlow apply the deletion; handlePostDelete fires the APIs
    resolve(true);
    setPendingDelete(null);
  }, [ensureDraft, setGraphData, pendingDelete, nodes, edges]);

  const handleCancelDelete = useCallback(() => {
    pendingDelete?.resolve(false);
    setPendingDelete(null);
  }, [pendingDelete]);

  const handlePostDelete = useCallback(
    async ({ nodes: deletedNodes, edges: deletedEdges }) => {
      const snapshot = snapshotRef.current;
      const { currentAgent } = useAgentPlaygroundStore.getState();

      // Edge-only: ensureDraft not yet called — store already reflects the deletion
      if (deletedNodes.length === 0) {
        const draftResult = await ensureDraft({ skipDirtyCheck: true });
        if (draftResult === false) {
          if (snapshot) setGraphData(snapshot.nodes, snapshot.edges);
          snapshotRef.current = null;
          return;
        }
        if (draftResult === "created") {
          snapshotRef.current = null;
          return;
        }
        // Already a draft — fire deleteConnectionApi for each edge
        const edgeResults = await Promise.allSettled(
          deletedEdges.map((edge) =>
            deleteConnectionApi({
              graphId: currentAgent?.id,
              versionId: currentAgent?.version_id,
              connectionId: edge.id,
            }),
          ),
        );
        const failedEdges = edgeResults.filter((r) => r.status === "rejected");
        if (failedEdges.length > 0) {
          logger.error(
            "[GraphView] deleteConnectionApi failed",
            failedEdges.map((r) => r.reason),
          );
          if (snapshot) setGraphData(snapshot.nodes, snapshot.edges);
          enqueueSnackbar("Failed to delete connection(s)", {
            variant: "error",
          });
        } else {
          queryClient.invalidateQueries({
            queryKey: [
              "agent-playground",
              "graph-versions",
              currentAgent?.id,
              currentAgent?.version_id,
            ],
          });
          queryClient.invalidateQueries({
            queryKey: ["agent-playground", "possible-edge-mappings"],
          });
        }
        snapshotRef.current = null;
        return;
      }

      // Node deletion — ensureDraft already called in handleConfirmDelete
      const results = await Promise.allSettled([
        ...deletedNodes.map((node) =>
          deleteNodeApi({
            graphId: currentAgent?.id,
            versionId: currentAgent?.version_id,
            nodeId: node.id,
          }),
        ),
        ...deletedEdges.map((edge) =>
          deleteConnectionApi({
            graphId: currentAgent?.id,
            versionId: currentAgent?.version_id,
            connectionId: edge.id,
          }),
        ),
      ]);

      const failedResults = results.filter((r) => r.status === "rejected");
      if (failedResults.length > 0) {
        logger.error(
          "[GraphView] deleteNodeApi/deleteConnectionApi failed",
          failedResults.map((r) => r.reason),
        );
        if (snapshot) setGraphData(snapshot.nodes, snapshot.edges);
        enqueueSnackbar("Failed to delete node(s)", { variant: "error" });
      } else {
        queryClient.invalidateQueries({
          queryKey: [
            "agent-playground",
            "graph-versions",
            currentAgent?.id,
            currentAgent?.version_id,
          ],
        });
        queryClient.invalidateQueries({
          queryKey: ["agent-playground", "possible-edge-mappings"],
        });
      }
      snapshotRef.current = null;
    },
    [setGraphData, ensureDraft, queryClient],
  );

  const onConnect = useCallback(
    async (connection) => {
      if (isRunning) return;

      const edgeId = crypto.randomUUID();

      // Always apply optimistic edit first
      const prevEdges = useAgentPlaygroundStore.getState().edges;
      storeOnConnect({ ...connection, id: edgeId });

      const draftResult = await ensureDraft();

      if (draftResult === false) {
        // POST failed — rollback
        const { nodes: curNodes } = useAgentPlaygroundStore.getState();
        setGraphData(curNodes, prevEdges);
        return;
      }

      if (draftResult === "created") {
        // Edge was included in the POST, IDs remapped. Done!
        return;
      }

      // Already a draft — fire individual API call
      const { currentAgent } = useAgentPlaygroundStore.getState();
      try {
        await createConnectionApi({
          graphId: currentAgent?.id,
          versionId: currentAgent?.version_id,
          data: {
            id: edgeId,
            source_node_id: connection.source,
            target_node_id: connection.target,
          },
        });
        // Refetch edge mappings now that the connection exists on the backend
        queryClient.invalidateQueries({
          queryKey: ["agent-playground", "possible-edge-mappings"],
        });
      } catch (error) {
        logger.error("[GraphView] createConnectionApi failed", error);
        const { nodes: curNodes } = useAgentPlaygroundStore.getState();
        setGraphData(curNodes, prevEdges);
        enqueueSnackbar("Failed to save connection", { variant: "error" });
      }
    },
    [storeOnConnect, ensureDraft, setGraphData, isRunning, queryClient],
  );

  const onNodeDragStop = useCallback(
    (_event, _node, nodes) => {
      const draggedIds = nodes.map((n) => n.id);
      const debounceKey = draggedIds.sort().join(",");

      if (positionDebounceRef.current[debounceKey]) {
        clearTimeout(positionDebounceRef.current[debounceKey]);
      }

      positionDebounceRef.current[debounceKey] = setTimeout(async () => {
        const { currentAgent, _isDraftCreating } =
          useAgentPlaygroundStore.getState();

        // Don't persist positions if not a draft, or if a draft creation is
        // in-flight (the version ID hasn't switched to the new draft yet).
        if (!currentAgent?.is_draft || _isDraftCreating) {
          delete positionDebounceRef.current[debounceKey];
          return;
        }

        // Already a draft — fire individual PATCH for each node position
        Promise.all(
          nodes.map((n) =>
            updateNodeApi({
              graphId: currentAgent?.id,
              versionId: currentAgent?.version_id,
              nodeId: n.id,
              data: { position: n.position },
            }),
          ),
        ).catch((error) => {
          logger.error("[GraphView] updateNodeApi position failed", error);
          onNodesChange(
            nodes.map((n) => ({
              type: "position",
              id: n.id,
              position: dragStartPositionRef.current[n.id],
            })),
          );
          enqueueSnackbar("Failed to save positions", { variant: "error" });
        });
        delete positionDebounceRef.current[debounceKey];
      }, 500);
    },
    [onNodesChange],
  );

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const { addNode } = useAddNodeOptimistic();

  const onDrop = useCallback(
    (event) => {
      event.preventDefault();
      if (isRunning) return;

      const type = event.dataTransfer.getData("application/reactflow");

      if (typeof type === "undefined" || !type) {
        return;
      }

      const nodeTemplateId =
        event.dataTransfer.getData("application/node-template-id") || undefined;

      const position = screenToFlowPosition({
        x: event.clientX - DROP_OFFSET_X,
        y: event.clientY - DROP_OFFSET_Y,
      });

      addNode({
        type,
        position,
        node_template_id: nodeTemplateId,
      });
    },
    [screenToFlowPosition, addNode, isRunning],
  );

  const onConnectStart = useCallback(
    (event, { nodeId }) => {
      // Connection started - track the SOURCE node (where user started dragging)
      setIsConnecting(true);
      setConnectingFromNodeId(nodeId ?? null);
    },
    [setIsConnecting, setConnectingFromNodeId],
  );

  const onConnectEnd = useCallback(() => {
    // Clear connection tracking when connection ends (successful or cancelled)
    setIsConnecting(false);
    setConnectingFromNodeId(null);
  }, [setIsConnecting, setConnectingFromNodeId]);

  const defaultEdgeOptions = useMemo(
    () => ({
      type: "animated",
    }),
    [],
  );

  return (
    <>
      <ReactFlow
        deleteKeyCode={isRunning ? null : ["Backspace", "Delete"]}
        nodesDraggable={!isRunning}
        nodesConnectable={!isRunning}
        elementsSelectable={!isRunning}
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDragStart={onNodeDragStart}
        onNodeDragStop={onNodeDragStop}
        onConnectStart={onConnectStart}
        onConnectEnd={onConnectEnd}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onBeforeDelete={onBeforeDelete}
        onDelete={handlePostDelete}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        connectionLineType={ConnectionLineType.SmoothStep}
        proOptions={proOptions}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
        defaultViewport={DEFAULT_VIEWPORT}
      >
        <Controls
          showZoom
          showInteractive={false}
          showFitView={false}
          position="bottom-right"
          style={{
            backgroundColor: "var(--bg-paper)",
            border: "1px solid var(--border-default)",
            borderRadius: "8px",
          }}
        />
      </ReactFlow>
      <ConfirmationDialog
        open={!!pendingDelete}
        onClose={handleCancelDelete}
        onConfirm={handleConfirmDelete}
        title={pendingDelete?.nodes.length > 1 ? "Delete Nodes" : "Delete Node"}
        message={
          pendingDelete?.nodes.length > 1
            ? `Are you sure you want to delete ${pendingDelete.nodes.length} nodes?`
            : `Are you sure you want to delete "${pendingDelete?.nodes[0]?.data?.label || "this node"}"?`
        }
        confirmText="Delete"
        cancelText="Cancel"
        confirmColor="error"
      />
    </>
  );
}
