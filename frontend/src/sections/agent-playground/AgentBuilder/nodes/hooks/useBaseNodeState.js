import { useNodeConnections, useNodeId } from "@xyflow/react";
import {
  useAgentPlaygroundStoreShallow,
  useWorkflowRunStoreShallow,
} from "../../../store";
import useCanEditAgent from "../../../hooks/useCanEditAgent";

const NODE_MIN_HEIGHT = 40;
const EMPTY_CONNECTIONS = [];

export default function useBaseNodeState({ id, data }) {
  const { preview } = data;

  // Guard: useNodeConnections requires a valid React Flow node context
  const contextNodeId = useNodeId();
  const incomingConnections = useNodeConnections(
    contextNodeId ? { handleType: "target" } : { handleType: "target", id },
  );
  const outgoingConnections = useNodeConnections(
    contextNodeId ? { handleType: "source" } : { handleType: "source", id },
  );
  const hasIncomingEdge =
    (contextNodeId ? incomingConnections : EMPTY_CONNECTIONS).length > 0;
  const hasOutgoingEdge =
    (contextNodeId ? outgoingConnections : EMPTY_CONNECTIONS).length > 0;

  // Store state (empty object in preview mode)
  const {
    setSelectedNode,
    deleteNode,
    selected,
    hasValidationError,
    executionState,
  } = useAgentPlaygroundStoreShallow((state) =>
    preview
      ? {}
      : {
          setSelectedNode: state.setSelectedNode,
          deleteNode: state.deleteNode,
          selected: state.selectedNode?.id === id,
          hasValidationError:
            state.validationErrorNodeIds?.includes(id) ?? false,
          executionState: state.nodeExecutionStates?.[id] || "idle",
        },
  );
  const isWorkflowRunning = useWorkflowRunStoreShallow((s) => s.isRunning);
  const { isReadOnly } = useCanEditAgent();
  const executionStateVal = executionState || "idle";
  const isRunning = executionStateVal === "running";
  const isCompleted = executionStateVal === "completed";
  const isError = executionStateVal === "error";
  const isIdleState = executionStateVal === "idle";

  const nodeHeight = NODE_MIN_HEIGHT;

  return {
    nodeHeight,
    selected,
    hasValidationError,
    isRunning,
    isCompleted,
    isError,
    isIdleState,
    hasIncomingEdge,
    hasOutgoingEdge,
    isWorkflowRunning,
    isReadOnly,
    preview,
    // Passed through for actions hook
    setSelectedNode,
    deleteNode,
  };
}
