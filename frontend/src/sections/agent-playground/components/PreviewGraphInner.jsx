import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, CircularProgress, Typography } from "@mui/material";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  ReactFlowProvider,
  MarkerType,
  Controls,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { PromptNode, AgentNode, EvalNode } from "../AgentBuilder/nodes";
import { NODE_TYPES } from "../utils/constants";

// Reuse existing node types
const nodeTypes = {
  [NODE_TYPES.LLM_PROMPT]: PromptNode,
  [NODE_TYPES.AGENT]: AgentNode,
  [NODE_TYPES.EVAL]: EvalNode,
};

// Wrapper component for preview container styling
export const PreviewContainer = ({
  children,
  isError,
  centered,
  height = 262,
  sx = {},
}) => (
  <Box
    sx={{
      height,
      width: "100%",
      border: "1px solid",
      borderColor: isError ? "error.main" : "divider",
      borderRadius: 0.25,
      overflow: "hidden",
      ...(centered && {
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }),
      ...(isError && { bgcolor: "error.lighter" }),
      ...sx,
    }}
  >
    {children}
  </Box>
);

PreviewContainer.propTypes = {
  children: PropTypes.node.isRequired,
  isError: PropTypes.bool,
  centered: PropTypes.bool,
  height: PropTypes.number,
  sx: PropTypes.object,
};

// Reactive fitView — calls fitView() whenever nodes change
const FitViewOnChange = ({ nodes, fitViewOptions }) => {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const timer = setTimeout(() => {
      fitView(fitViewOptions);
    }, 0);
    return () => clearTimeout(timer);
  }, [nodes, fitView, fitViewOptions]);
  return null;
};

FitViewOnChange.propTypes = {
  nodes: PropTypes.array.isRequired,
  fitViewOptions: PropTypes.object,
};

// Inner graph component - reusable across different preview contexts
export const PreviewGraphInner = ({
  nodes,
  edges,
  showControls = false,
  backgroundVariant = BackgroundVariant.Dots,
  backgroundColor = "var(--bg-paper)",
  canvasBackground = "transparent",
  fitView = false,
  fitViewOptions,
}) => {
  const [ready, setReady] = useState(!fitView);

  // Add preview: true to all node data to disable interactions
  const previewNodes = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          preview: true,
        },
      })),
    [nodes],
  );

  const defaultEdgeOptions = useMemo(
    () => ({
      type: "smoothstep",
      style: {
        strokeDasharray: "8 6",
        stroke: "var(--border-hover)",
        strokeWidth: 2,
      },
      markerEnd: {
        type: MarkerType.Arrow,
        width: 16,
        height: 16,
        color: "var(--border-hover)",
      },
    }),
    [],
  );

  // When fitView is enabled, wait for onInit to call fitView before revealing
  const handleInit = useCallback(
    (instance) => {
      if (fitView) {
        // fitView returns a boolean; give nodes one frame to measure, then reveal
        requestAnimationFrame(() => {
          instance.fitView(fitViewOptions);
          setReady(true);
        });
      }
    },
    [fitView, fitViewOptions],
  );

  return (
    <ReactFlow
      nodes={previewNodes}
      edges={edges}
      nodeTypes={nodeTypes}
      defaultEdgeOptions={defaultEdgeOptions}
      proOptions={{ hideAttribution: true }}
      style={{
        background: canvasBackground,
        opacity: ready ? 1 : 0,
        transition: "opacity 0.2s ease-in",
      }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      panOnDrag
      zoomOnScroll
      zoomOnPinch
      zoomOnDoubleClick={false}
      minZoom={0.3}
      maxZoom={2}
      defaultViewport={{ x: 40, y: 400, zoom: 0.8 }}
      onInit={handleInit}
    >
      {backgroundVariant ? (
        <Background
          id="preview-bg"
          variant={backgroundVariant}
          color={backgroundColor}
          gap={20}
          size={2}
        />
      ) : null}
      {fitView && (
        <FitViewOnChange nodes={previewNodes} fitViewOptions={fitViewOptions} />
      )}
      {showControls && (
        <Controls showInteractive={false} position="bottom-right" />
      )}
    </ReactFlow>
  );
};

PreviewGraphInner.propTypes = {
  nodes: PropTypes.array.isRequired,
  edges: PropTypes.array.isRequired,
  showControls: PropTypes.bool,
  backgroundVariant: PropTypes.oneOf([
    BackgroundVariant.Dots,
    BackgroundVariant.Lines,
    BackgroundVariant.Cross,
  ]),
  backgroundColor: PropTypes.string,
  canvasBackground: PropTypes.string,
  fitView: PropTypes.bool,
  fitViewOptions: PropTypes.object,
};

// Loading state component
export const PreviewLoading = ({ height = 262 }) => (
  <PreviewContainer centered height={height}>
    <CircularProgress size={24} />
  </PreviewContainer>
);

PreviewLoading.propTypes = {
  height: PropTypes.number,
};

// Error state component
export const PreviewError = ({
  height = 262,
  message = "Failed to load graph preview",
}) => (
  <PreviewContainer isError centered height={height}>
    <Typography color="error" variant="body2">
      {message}
    </Typography>
  </PreviewContainer>
);

PreviewError.propTypes = {
  height: PropTypes.number,
  message: PropTypes.string,
};

// Complete preview wrapper with ReactFlowProvider
export const GraphPreviewWrapper = ({
  nodes,
  edges,
  height = 262,
  showControls = false,
  backgroundVariant,
  backgroundColor,
}) => (
  <PreviewContainer height={height}>
    <ReactFlowProvider>
      <PreviewGraphInner
        nodes={nodes}
        edges={edges}
        showControls={showControls}
        backgroundVariant={backgroundVariant}
        backgroundColor={backgroundColor}
      />
    </ReactFlowProvider>
  </PreviewContainer>
);

GraphPreviewWrapper.propTypes = {
  nodes: PropTypes.array.isRequired,
  edges: PropTypes.array.isRequired,
  height: PropTypes.number,
  showControls: PropTypes.bool,
  backgroundVariant: PropTypes.oneOf([
    BackgroundVariant.Dots,
    BackgroundVariant.Lines,
    BackgroundVariant.Cross,
  ]),
  backgroundColor: PropTypes.string,
};

export default PreviewGraphInner;
