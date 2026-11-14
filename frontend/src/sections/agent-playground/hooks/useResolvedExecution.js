import { useMemo } from "react";

const getNodeExecution = (node) => node?.nodeExecution || node?.node_execution;

/**
 * Resolve a React Flow node ID to the correct executionId and nodeExecutionId
 * for the node-execution-detail API call.
 *
 * Subgraph inner nodes use `parentId__innerNodeId` format — for those,
 * the execution context is `subGraph.id` (not the root execution).
 */
export default function useResolvedExecution({
  selectedNodeId,
  executionData,
  executionId,
}) {
  const nodeExecutionId = useMemo(() => {
    if (!selectedNodeId || !executionData?.nodes) return null;
    if (selectedNodeId?.includes("__")) {
      const [parentId, innerOriginalId] = selectedNodeId.split("__");
      const parentNode = executionData.nodes.find((n) => n.id === parentId);
      const innerNodes = (parentNode?.subGraph || parentNode?.sub_graph)?.nodes;
      const innerNode = innerNodes?.find((n) => n.id === innerOriginalId);
      return getNodeExecution(innerNode)?.id || null;
    }
    const node = executionData.nodes.find((n) => n.id === selectedNodeId);
    return getNodeExecution(node)?.id || null;
  }, [selectedNodeId, executionData]);

  //graph execution id
  const resolvedExecutionId = useMemo(() => {
    if (!selectedNodeId || !executionData?.nodes) return executionId;
    if (selectedNodeId.includes("__")) {
      const [parentId] = selectedNodeId.split("__");
      const parentNode = executionData.nodes.find((n) => n.id === parentId);
      const subGraph = parentNode?.subGraph || parentNode?.sub_graph;
      return subGraph?.id ?? null;
    }
    return executionId;
  }, [selectedNodeId, executionData, executionId]);

  return { nodeExecutionId, resolvedExecutionId };
}
