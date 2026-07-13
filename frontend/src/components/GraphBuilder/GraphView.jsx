import React, { useCallback, useEffect, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  ConnectionLineType,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import ConversationNode from "./nodes/ConversationNode";
import EndCallNode from "./nodes/EndCallNode";
import TransferCallNode from "./nodes/TransferCallNode";
import EndChatNode from "./nodes/EndChatNode";
import TransferChatNode from "./nodes/TransferChatNode";
import ConditionEdge from "./edges/ConditionEdge";
import PropTypes from "prop-types";
import { ShowComponent } from "../show";
import { useTheme } from "@mui/material";
import { useGraphStore } from "./store/graphStore";

const nodeTypes = {
  conversation: ConversationNode,
  end: EndCallNode,
  transfer: TransferCallNode,
  endChat: EndChatNode,
  transferChat: TransferChatNode,
};

const edgeTypes = {
  condition: ConditionEdge,
};

const GraphView = ({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onDragOver,
  onNodeClick,
  showMiniMap,
  nodeTypesOverride,
  editMode = false,
}) => {
  const effectiveNodeTypes = useMemo(
    () =>
      nodeTypesOverride ? { ...nodeTypes, ...nodeTypesOverride } : nodeTypes,
    [nodeTypesOverride],
  );
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  const { screenToFlowPosition, fitView } = useReactFlow();

  const orphanFocusSignal = useGraphStore((state) => state.orphanFocusSignal);

  useEffect(() => {
    if (!editMode || orphanFocusSignal === 0) return;
    const highlightedNodes = useGraphStore
      .getState()
      .nodes.filter((node) => node?.data?.highlightColor === "error")
      .map((node) => ({ id: node.id }));
    if (highlightedNodes.length === 0) return;
    fitView({
      nodes: highlightedNodes,
      padding: 0.4,
      duration: 500,
      maxZoom: 1,
    });
  }, [orphanFocusSignal, editMode, fitView]);

  const onDrop = useCallback(
    (event) => {
      event.preventDefault();

      const type = event.dataTransfer.getData("application/reactflow");

      if (typeof type === "undefined" || !type) {
        return;
      }

      const position = screenToFlowPosition({
        x: event.clientX - 100,
        y: event.clientY,
      });

      useGraphStore.getState().addNode(type, position, editMode);
    },
    [screenToFlowPosition, editMode],
  );

  const defaultEdgeOptions = useMemo(
    () => ({
      type: "condition",
    }),
    [],
  );
  const proOptions = { hideAttribution: true };
  return (
    <>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onDrop={onDrop}
        onDragOver={onDragOver}
        nodeTypes={effectiveNodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        connectionLineType={ConnectionLineType.Bezier}
        proOptions={proOptions}
        style={{ backgroundColor: "var(--bg-paper)" }}
        defaultViewport={{ x: 0, y: 0, zoom: 0.1 }}
        onlyRenderVisibleElements
        fitView={true}
        fitViewOptions={{ padding: 0.2, maxZoom: 1.2, minZoom: 0.3 }}
      >
        <Background
          variant="dots"
          color="var(--border-light)"
          gap={10}
          size={2}
        />
        <Controls
          showZoom
          showFitView={true}
          style={{
            backgroundColor: "var(--bg-paper)",
            border: "1px solid var(--border-default)",
            borderRadius: "8px",
          }}
        />
        <ShowComponent condition={editMode || showMiniMap}>
          <MiniMap
            style={{
              backgroundColor: isDark ? "#1f1f23" : "var(--bg-paper)",
              border: "1px solid var(--border-default)",
              borderRadius: "8px",
            }}
            maskColor={
              isDark ? "rgba(10, 10, 10, 0.6)" : "rgba(240, 240, 240, 0.6)"
            }
            pannable
            nodeColor={(node) => {
              if (isDark) {
                switch (node.type) {
                  case "conversation":
                    return "#3f3f46";
                  case "end":
                  case "endChat":
                    return "#7F1A12";
                  case "transfer":
                  case "transferChat":
                    return "#8C3F08";
                  default:
                    return "#3f3f46";
                }
              }
              switch (node.type) {
                case "conversation":
                  return theme.palette.primary.main;
                case "end":
                case "endChat":
                  return theme.palette.red[500];
                case "transfer":
                case "transferChat":
                  return theme.palette.orange[500];
                default:
                  return theme.palette.text.disabled;
              }
            }}
            nodeStrokeColor={(node) => {
              if (isDark) {
                switch (node.type) {
                  case "conversation":
                    return "#71717a";
                  case "end":
                  case "endChat":
                    return "#D92D20";
                  case "transfer":
                  case "transferChat":
                    return "#E9690C";
                  default:
                    return "#52525b";
                }
              }
              switch (node.type) {
                case "conversation":
                  return theme.palette.primary.main;
                case "end":
                case "endChat":
                  return theme.palette.red[600];
                case "transfer":
                case "transferChat":
                  return theme.palette.orange[600];
                default:
                  return theme.palette.text.disabled;
              }
            }}
            nodeStrokeWidth={2}
          />
        </ShowComponent>
      </ReactFlow>
    </>
  );
};

GraphView.propTypes = {
  nodes: PropTypes.array,
  edges: PropTypes.array,
  onNodesChange: PropTypes.func,
  onEdgesChange: PropTypes.func,
  onConnect: PropTypes.func,
  onDrop: PropTypes.func,
  onDragOver: PropTypes.func,
  onNodeClick: PropTypes.func,
  showMiniMap: PropTypes.bool,
  nodeTypesOverride: PropTypes.object,
  editMode: PropTypes.bool,
  highlightedNodeNames: PropTypes.array,
};

export default GraphView;
