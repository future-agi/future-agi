import { Box } from "@mui/material";
import React, { useCallback, useRef, useState, useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import { AgentGraph } from "src/components/AgentGraph";
import { START_ID, END_ID } from "src/components/AgentGraph/layoutUtils";
import useResolvedExecution from "../../hooks/useResolvedExecution";
import { useWorkflowRunStoreShallow, useAgentPlaygroundStore } from "../../store";
import NodeOutputDetail from "./NodeOutputDetail";
import NodeOutputListView from "./NodeOutputListView";
import { mapExecutionNodesToTree } from "./common";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import PanelErrorBoundary from "../../components/PanelErrorBoundary";

const MIN_PANEL_HEIGHT = 200;
const MAX_PANEL_HEIGHT = 600;

export default function RunAgentPanel({
  panelHeight,
  onResize,
  executionId,
  executionData,
}) {
  const resizeRef = useRef(null);
  const [isResizing, setIsResizing] = useState(false);
  const isRunning = useWorkflowRunStoreShallow((s) => s.isRunning);
  const currentAgent = useAgentPlaygroundStore((s) => s.currentAgent);
  const [selectedNodeId, setSelectedNodeId] = useState(null);

  const treeNodes = useMemo(
    () => mapExecutionNodesToTree(executionData),
    [executionData],
  );

  // Reset selected node when a new execution starts
  useEffect(() => {
    if (!executionId) {
      setSelectedNodeId(null);
    }
  }, [executionId]);

  // Auto-select: first executed node during run, last executed node on completion
  useEffect(() => {
    if (!executionData?.nodes?.length || selectedNodeId) return;

    const executedNodes = executionData.nodes.filter(
      (n) => n.nodeExecution || n.node_execution,
    );
    if (executedNodes.length === 0) return;

    if (isRunning) {
      // During running, select the first executed node (once)
      setSelectedNodeId(executedNodes[0].id);
    } else {
      // On completion, select the last executed node
      const lastNode = executedNodes[executedNodes.length - 1];
      if (lastNode.subGraph?.nodes?.length) {
        const executedInner = lastNode.subGraph.nodes.filter(
          (n) => n.nodeExecution || n.node_execution,
        );
        if (executedInner.length > 0) {
          const lastInner = executedInner[executedInner.length - 1];
          setSelectedNodeId(`${lastNode.id}__${lastInner.id}`);
          return;
        }
      }
      setSelectedNodeId(lastNode.id);
    }
  }, [executionData, selectedNodeId, isRunning]);

  const { nodeExecutionId: selectedNodeExecutionId, resolvedExecutionId } =
    useResolvedExecution({ selectedNodeId, executionData, executionId });

  // Handle resize
  const handleMouseMove = useCallback(
    (e) => {
      if (!isResizing) return;
      const newHeight = window.innerHeight - e.clientY;
      const clampedHeight = Math.max(
        MIN_PANEL_HEIGHT,
        Math.min(MAX_PANEL_HEIGHT, newHeight),
      );
      onResize(clampedHeight);
    },
    [isResizing, onResize],
  );

  const handleMouseUp = useCallback(() => {
    setIsResizing(false);
  }, []);

  useEffect(() => {
    if (isResizing) {
      document.addEventListener("mousemove", handleMouseMove);
      document.addEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "row-resize";
      document.body.style.userSelect = "none";
    } else {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isResizing, handleMouseMove, handleMouseUp]);

  const handleResizeStart = (e) => {
    e.preventDefault();
    setIsResizing(true);
  };

  const handleGraphNodeClick = useCallback((_event, node) => {
    if (node.id === START_ID || node.id === END_ID) return;
    setSelectedNodeId(node.id);
  }, []);

  return (
    <Box
      sx={{
        position: "absolute",
        bottom: 0,
        left: 0,
        right: 0,
        height: panelHeight,
        bgcolor: "background.paper",
        borderTop: "1px solid",
        borderColor: "divider",
        display: "flex",
        flexDirection: "column",
        zIndex: 10,
      }}
    >
      {/* Resize handle */}
      <Box
        ref={resizeRef}
        onMouseDown={handleResizeStart}
        sx={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 4,
          cursor: "row-resize",
          zIndex: 1,
          "&:hover": {
            backgroundColor: "primary.main",
          },
          ...(isResizing && {
            backgroundColor: "primary.main",
          }),
        }}
      />
      <ResizablePanels
        initialLeftWidth={60}
        minLeftWidth={20}
        maxLeftWidth={80}
        leftPanel={
          <Box sx={{ display: "flex", height: "100%", width: "100%", overflow: "hidden" }}>
            <PanelErrorBoundary
              name="NodeOutputListView"
              onRetry={() => setSelectedNodeId(null)}
            >
              <NodeOutputListView
                currentAgent={currentAgent}
                nodes={treeNodes}
                selectedNodeId={selectedNodeId}
                onNodeSelect={setSelectedNodeId}
              />
            </PanelErrorBoundary>
            <Box
              sx={{
                flex: 1,
                height: "100%",
                minWidth: 0,
                borderLeft: "1px solid",
                borderColor: "divider",
              }}
            >
              <AgentGraph
                executionData={executionData}
                onNodeClick={handleGraphNodeClick}
                selectedNodeId={selectedNodeId}
              />
            </Box>
          </Box>
        }
        rightPanel={
          <PanelErrorBoundary
            name="NodeOutputDetail"
            onRetry={() => setSelectedNodeId(null)}
          >
            <NodeOutputDetail
              executionId={resolvedExecutionId}
              nodeExecutionId={selectedNodeExecutionId}
            />
          </PanelErrorBoundary>
        }
      />
    </Box>
  );
}

RunAgentPanel.propTypes = {
  panelHeight: PropTypes.number.isRequired,
  onResize: PropTypes.func.isRequired,
  executionId: PropTypes.string,
  executionData: PropTypes.object,
};
