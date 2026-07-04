/**
 * Hook that polls execution detail and syncs node/edge visual states.
 * Manages workflow status transitions (completed, error) and
 * maps API node statuses to canvas execution states.
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useGetExecutionDetail } from "src/api/agent-playground/agent-playground";
import {
  useAgentPlaygroundStore,
  useAgentPlaygroundStoreShallow,
  useWorkflowRunStoreShallow,
} from "../store";
import {
  NODE_EXECUTION_STATE,
  EXECUTION_STATUS,
  mapApiStatusToNodeState,
} from "../utils/workflowExecution";
import { EDGE_STATE } from "../utils/constants";
import { enqueueSnackbar } from "src/components/snackbar";

/**
 * Build edge execution states based on the connected nodes' states.
 */
function buildEdgeStates(nodeStatesMap, edges) {
  const edgeStatesMap = {};
  for (const edge of edges) {
    const srcState = nodeStatesMap[edge.source];
    const tgtState = nodeStatesMap[edge.target];
    if (
      srcState === NODE_EXECUTION_STATE.COMPLETED &&
      tgtState === NODE_EXECUTION_STATE.RUNNING
    ) {
      edgeStatesMap[edge.id] = EDGE_STATE.ACTIVE;
    } else if (
      srcState === NODE_EXECUTION_STATE.COMPLETED &&
      tgtState === NODE_EXECUTION_STATE.COMPLETED
    ) {
      edgeStatesMap[edge.id] = EDGE_STATE.COMPLETED;
    } else if (srcState === NODE_EXECUTION_STATE.COMPLETED && !tgtState) {
      edgeStatesMap[edge.id] = EDGE_STATE.WAITING;
    }
  }
  return edgeStatesMap;
}

export default function useExecutionSync(graphId, executionId) {
  const queryClient = useQueryClient();
  const { data: executionData } = useGetExecutionDetail(graphId, executionId);

  const { completeRun, failRun } = useWorkflowRunStoreShallow((s) => ({
    completeRun: s.completeRun,
    failRun: s.failRun,
  }));

  const { setNodeExecutionStates, setEdgeExecutionStates } =
    useAgentPlaygroundStoreShallow((s) => ({
      setNodeExecutionStates: s.setNodeExecutionStates,
      setEdgeExecutionStates: s.setEdgeExecutionStates,
    }));

  // Track workflow-level status transitions (fire once per status change)
  const executionStatusRef = useRef(null);
  // Track per-node statuses for query invalidation on terminal transitions
  const nodeStatusesRef = useRef({});

  useEffect(() => {
    if (!executionId) {
      executionStatusRef.current = null;
      nodeStatusesRef.current = {};
      return;
    }
    if (!executionData?.status) return;
    const status = executionData.status.toLowerCase();
    if (status === executionStatusRef.current) return;
    executionStatusRef.current = status;

    if (status === EXECUTION_STATUS.SUCCESS) {
      completeRun(executionData, null);
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph-executions", graphId],
      });
    } else if (
      status === EXECUTION_STATUS.ERROR ||
      status === EXECUTION_STATUS.FAILED
    ) {
      const executionError = executionData.error_message || "Execution failed";
      failRun(executionError, executionData);
      enqueueSnackbar(executionError, {
        variant: "error",
      });
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph-executions", graphId],
      });
    }
  }, [
    graphId,
    executionId,
    executionData,
    completeRun,
    failRun,
    setNodeExecutionStates,
    setEdgeExecutionStates,
    queryClient,
  ]);

  // Sync per-node and per-edge execution states for visual feedback
  useEffect(() => {
    if (!executionId || !executionData?.nodes) return;

    const nodeStatesMap = {};
    const newNodeStatuses = {};
    for (const node of executionData.nodes) {
      const nodeExecution = node.node_execution;
      const currentStatus = nodeExecution?.status?.toLowerCase();
      const nodeState = mapApiStatusToNodeState(currentStatus);
      if (nodeState) {
        nodeStatesMap[node.id] = nodeState;
      }

      // Invalidate node detail query when node reaches terminal status
      const nodeExecId = nodeExecution?.id;
      const prevStatus = nodeStatusesRef.current[node.id];
      if (nodeExecId && currentStatus && currentStatus !== prevStatus) {
        const isTerminal =
          currentStatus === EXECUTION_STATUS.SUCCESS ||
          currentStatus === EXECUTION_STATUS.ERROR ||
          currentStatus === EXECUTION_STATUS.FAILED ||
          currentStatus === EXECUTION_STATUS.SKIPPED;
        if (isTerminal) {
          queryClient.invalidateQueries({
            queryKey: [
              "agent-playground",
              "node-execution-detail",
              executionId,
              nodeExecId,
            ],
          });
        }
      }
      newNodeStatuses[node.id] = currentStatus;
    }
    nodeStatusesRef.current = newNodeStatuses;
    setNodeExecutionStates(nodeStatesMap);

    // Clear edge animations on terminal failure; otherwise derive from node states
    const status = executionData.status?.toLowerCase();
    if (
      status === EXECUTION_STATUS.ERROR ||
      status === EXECUTION_STATUS.FAILED
    ) {
      setEdgeExecutionStates({});
    } else {
      const currentEdges = useAgentPlaygroundStore.getState().edges;
      setEdgeExecutionStates(buildEdgeStates(nodeStatesMap, currentEdges));
    }
  }, [
    executionId,
    executionData,
    setNodeExecutionStates,
    setEdgeExecutionStates,
  ]);

  return { executionData, executionStatusRef };
}
