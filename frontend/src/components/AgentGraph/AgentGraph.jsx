import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, CircularProgress, Typography } from "@mui/material";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import ExecutionNode from "./ExecutionNode";
import StartEndNode from "./StartEndNode";
import SubgraphGroupNode from "./SubgraphGroupNode";
import { buildExecutionGraph } from "./layoutUtils";
import { isSkippedExecutionStatus } from "./nodeUtils";
import "./agent-graph-animations.css";

const nodeTypes = {
  executionNode: ExecutionNode,
  startEndNode: StartEndNode,
  subgraphGroup: SubgraphGroupNode,
};

function AgentGraphInner({ executionData, onNodeClick, selectedNodeId }) {
  const { nodes, edges } = useMemo(
    () => buildExecutionGraph(executionData),
    [executionData],
  );

  const nodesWithSelection = useMemo(
    () =>
      nodes.map((n) => ({
        ...n,
        data: { ...n.data, selected: n.id === selectedNodeId },
      })),
    [nodes, selectedNodeId],
  );

  const handleNodeClick = useCallback(
    (event, node) => {
      if (!node.data?.nodeExecution) return;
      if (isSkippedExecutionStatus(node.data.nodeExecution.status)) return;
      onNodeClick?.(event, node);
    },
    [onNodeClick],
  );

  return (
    <ReactFlow
      nodes={nodesWithSelection}
      edges={edges}
      nodeTypes={nodeTypes}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      onNodeClick={handleNodeClick}
      proOptions={{ hideAttribution: true }}
      fitView
      fitViewOptions={{ padding: 0.3, maxZoom: 1.2 }}
      minZoom={0.3}
      maxZoom={2}
    >
      <Background
        variant={BackgroundVariant.Dots}
        color="var(--bg-paper)"
        gap={20}
        size={2}
      />
      <Controls
        showFitView={false}
        showInteractive={false}
        position="bottom-right"
      />
    </ReactFlow>
  );
}

AgentGraphInner.propTypes = {
  executionData: PropTypes.object,
  onNodeClick: PropTypes.func,
  selectedNodeId: PropTypes.string,
};

export default function AgentGraph({
  executionData,
  onNodeClick,
  selectedNodeId,
  loading = false,
  title = "Agent Graph",
  height = "100%",
}) {
  if (loading || !executionData) {
    return (
      <Box
        sx={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <CircularProgress size={24} />
        <Typography variant="body2" color="text.secondary">
          Loading execution graph...
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ height, display: "flex", flexDirection: "column" }}>
      {title && (
        <Typography
          variant="subtitle2"
          sx={{ px: 2, pt: 1.5, pb: 0.5, flexShrink: 0 }}
        >
          {title}
        </Typography>
      )}
      <Box sx={{ flex: 1, minHeight: 0 }}>
        <ReactFlowProvider>
          <AgentGraphInner
            executionData={executionData}
            onNodeClick={onNodeClick}
            selectedNodeId={selectedNodeId}
          />
        </ReactFlowProvider>
      </Box>
    </Box>
  );
}

AgentGraph.propTypes = {
  executionData: PropTypes.object,
  onNodeClick: PropTypes.func,
  selectedNodeId: PropTypes.string,
  loading: PropTypes.bool,
  title: PropTypes.string,
  height: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};
