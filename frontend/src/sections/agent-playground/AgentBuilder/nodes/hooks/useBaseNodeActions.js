import { useCallback, useRef, useState } from "react";
import { useReactFlow } from "@xyflow/react";
import { enqueueSnackbar } from "notistack";
import {
  useAgentPlaygroundStore,
  useAgentPlaygroundStoreShallow,
  useWorkflowRunStore,
} from "../../../store";
import { NODE_X_OFFSET } from "../../../utils/constants";
import useAddNodeOptimistic from "../../hooks/useAddNodeOptimistic";
import { useSaveDraftContext } from "../../saveDraftContext";
import { deleteNodeApi } from "src/api/agent-playground/agent-playground";

export default function useBaseNodeActions({
  id,
  preview,
  isWorkflowRunning,
  isRunning,
  setSelectedNode,
  deleteNode,
}) {
  const { ensureDraft } = useSaveDraftContext();
  const { setCenter, getZoom, getNode } = useReactFlow();
  const setGraphData = useAgentPlaygroundStoreShallow(
    (state) => state.setGraphData,
  );
  const { addNode } = useAddNodeOptimistic();
  const [popperOpen, setPopperOpen] = useState(false);
  const addButtonRef = useRef(null);

  const handleNodeClick = useCallback(() => {
    if (preview || isWorkflowRunning) return;
    const node = getNode(id);
    if (node) {
      setSelectedNode(node);
    }
  }, [id, preview, isWorkflowRunning, getNode, setSelectedNode]);

  const handleAddClick = useCallback(
    (e) => {
      if (preview || isWorkflowRunning) return;
      e.stopPropagation();
      setPopperOpen(true);
    },
    [preview, isWorkflowRunning],
  );

  const handlePopperClose = useCallback(() => {
    setPopperOpen(false);
  }, []);

  const handleNodeSelect = useCallback(
    async (nodeType, nodeTemplateId, initialConfig) => {
      if (preview || isWorkflowRunning) return;
      const currentNode = getNode(id);
      const position = currentNode
        ? {
            x: currentNode.position.x + NODE_X_OFFSET,
            y: currentNode.position.y,
          }
        : undefined;

      const added = await addNode({
        type: nodeType,
        position,
        sourceNodeId: id,
        node_template_id: nodeTemplateId,
        name: initialConfig?.name,
        config: initialConfig,
      });

      if (added && position) {
        setCenter(position.x + 300, position.y, {
          duration: 800,
          zoom: getZoom(),
        });
      }

      setPopperOpen(false);
    },
    [id, preview, isWorkflowRunning, getNode, addNode, setCenter, getZoom],
  );

  const handleDeleteClick = useCallback(
    async (e) => {
      e.stopPropagation();
      e.preventDefault?.();

      const isLiveDeleteBlocked = () =>
        useWorkflowRunStore.getState().isRunning ||
        useAgentPlaygroundStore.getState().nodeExecutionStates?.[id] ===
          "running";

      if (preview || isWorkflowRunning || isRunning || isLiveDeleteBlocked()) {
        return;
      }

      // Always apply optimistic deletion first
      const { nodes, edges, selectedNode } = useAgentPlaygroundStore.getState();
      const nodeExistsBeforeDelete = nodes.some((n) => n.id === id);
      if (!nodeExistsBeforeDelete) return;

      const restoredSelectedNode =
        selectedNode?.id === id
          ? nodes.find((node) => node.id === id) ?? selectedNode
          : null;
      const restoreDeletedNode = () => {
        setGraphData(nodes, edges);
        if (restoredSelectedNode) {
          setSelectedNode(restoredSelectedNode);
        }
      };

      deleteNode(id);
      const deleteWasBlocked =
        nodeExistsBeforeDelete &&
        useAgentPlaygroundStore.getState().nodes.some((n) => n.id === id);
      if (deleteWasBlocked) return;

      if (isLiveDeleteBlocked()) {
        restoreDeletedNode();
        return;
      }

      const draftResult = await ensureDraft({ skipDirtyCheck: true });

      if (draftResult === false) {
        // Rollback
        restoreDeletedNode();
        return;
      }

      if (draftResult === "created") {
        // Deletion included in POST. Done!
        return;
      }

      if (isLiveDeleteBlocked()) {
        restoreDeletedNode();
        return;
      }

      // Already a draft — fire individual DELETE
      const { currentAgent } = useAgentPlaygroundStore.getState();
      try {
        await deleteNodeApi({
          graphId: currentAgent?.id,
          versionId: currentAgent?.version_id,
          nodeId: id,
        });
      } catch {
        restoreDeletedNode();
        enqueueSnackbar(
          "Couldn't delete node. Your changes were restored. Try again.",
          { variant: "error" },
        );
      }
    },
    [
      id,
      preview,
      isWorkflowRunning,
      isRunning,
      deleteNode,
      setGraphData,
      setSelectedNode,
      ensureDraft,
    ],
  );

  return {
    handleNodeClick,
    handleAddClick,
    handlePopperClose,
    handleNodeSelect,
    handleDeleteClick,
    popperOpen,
    addButtonRef,
  };
}
