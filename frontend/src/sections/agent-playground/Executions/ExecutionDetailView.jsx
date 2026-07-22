import React, { useCallback, useEffect, useRef, useState } from "react";
import { Box, CircularProgress, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { useQueryClient } from "@tanstack/react-query";
import { AgentGraph } from "src/components/AgentGraph";
import { START_ID, END_ID } from "src/components/AgentGraph/layoutUtils";
import NodeOutputDetail from "../AgentBuilder/RunAgentPanel/NodeOutputDetail";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import { useGetExecutionDetail } from "src/api/agent-playground/agent-playground";
import useResolvedExecution from "../hooks/useResolvedExecution";
import { EXECUTION_STATUS } from "../utils/workflowExecution";

export default function ExecutionDetailView({ graphId, executionId }) {
  const queryClient = useQueryClient();
  const {
    data: executionData,
    isLoading,
    isError,
  } = useGetExecutionDetail(graphId, executionId);

  const [selectedNodeId, setSelectedNodeId] = useState(null);

  // Invalidate executions list and node details when polling reaches terminal status
  const prevStatusRef = useRef(null);
  const nodeStatusesRef = useRef({});
  useEffect(() => {
    prevStatusRef.current = null;
    nodeStatusesRef.current = {};
  }, [executionId]);
  useEffect(() => {
    const status = executionData?.status?.toLowerCase();
    if (!status || status === prevStatusRef.current) return;
    prevStatusRef.current = status;

    if (
      status === EXECUTION_STATUS.SUCCESS ||
      status === EXECUTION_STATUS.ERROR ||
      status === EXECUTION_STATUS.FAILED
    ) {
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph-executions", graphId],
      });
    }
  }, [executionData, graphId, queryClient]);

  // Invalidate node-execution-detail when individual nodes reach terminal status
  useEffect(() => {
    if (!executionId || !executionData?.nodes) return;

    const newNodeStatuses = {};
    for (const node of executionData.nodes) {
      const nodeExecution = node.node_execution;
      const currentStatus = nodeExecution?.status?.toLowerCase();
      const nodeExecId = nodeExecution?.id;
      const prevNodeStatus = nodeStatusesRef.current[node.id];

      if (nodeExecId && currentStatus && currentStatus !== prevNodeStatus) {
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
  }, [executionData, executionId, queryClient]);

  // Reset node selection when execution changes, then auto-select last executed node
  useEffect(() => {
    if (!executionData?.nodes?.length) {
      setSelectedNodeId(null);
      return;
    }
    // Find last node that has a node_execution (skip pending nodes)
    const executedNodes = executionData.nodes.filter((n) => n.node_execution);
    if (executedNodes.length === 0) {
      setSelectedNodeId(null);
      return;
    }
    const lastNode = executedNodes[executedNodes.length - 1];
    const lastNodeSubGraph = lastNode.sub_graph;
    if (lastNodeSubGraph?.nodes?.length) {
      const executedInner = lastNodeSubGraph.nodes.filter(
        (n) => n.node_execution,
      );
      if (executedInner.length > 0) {
        const lastInner = executedInner[executedInner.length - 1];
        setSelectedNodeId(`${lastNode.id}__${lastInner.id}`);
        return;
      }
    }
    setSelectedNodeId(lastNode.id);
  }, [executionId, executionData]);

  const { nodeExecutionId: selectedNodeExecutionId, resolvedExecutionId } =
    useResolvedExecution({ selectedNodeId, executionData, executionId });

  const handleGraphNodeClick = useCallback((_event, node) => {
    if (node.id === START_ID || node.id === END_ID) return;
    setSelectedNodeId(node.id);
  }, []);

  if (!executionId) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography typography="s2" color="text.secondary">
          Select an execution to view details
        </Typography>
      </Box>
    );
  }

  if (isLoading) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <CircularProgress size={28} />
        <Typography typography="s2" color="text.secondary">
          Loading execution details...
        </Typography>
      </Box>
    );
  }

  const isPending =
    executionData?.status?.toLowerCase() === "pending" &&
    !selectedNodeExecutionId;

  if (isError) {
    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography typography="s2" color="error.main">
          Failed to load execution details
        </Typography>
      </Box>
    );
  }

  return (
    <ResizablePanels
      initialLeftWidth={50}
      minLeftWidth={15}
      maxLeftWidth={80}
      leftPanel={
        <AgentGraph
          executionData={executionData}
          onNodeClick={handleGraphNodeClick}
          selectedNodeId={selectedNodeId}
        />
      }
      rightPanel={
        isPending ? (
          <Box
            sx={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexDirection: "column",
              gap: 1,
              height: "100%",
            }}
          >
            <CircularProgress size={24} />
            <Typography typography="s2" color="text.secondary">
              Execution is waiting to start
            </Typography>
          </Box>
        ) : (
          <NodeOutputDetail
            executionId={resolvedExecutionId}
            nodeExecutionId={selectedNodeExecutionId}
          />
        )
      }
    />
  );
}

ExecutionDetailView.propTypes = {
  graphId: PropTypes.string.isRequired,
  executionId: PropTypes.string,
};
