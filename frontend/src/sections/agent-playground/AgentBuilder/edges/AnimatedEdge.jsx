/**
 * Animated Edge Component for workflow execution visualization
 * Shows different states: idle, active (dashes moving left to right), completed
 * Hover: shows "+" (add node) and delete buttons at the midpoint
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  getSmoothStepPath,
  EdgeLabelRenderer,
  useReactFlow,
} from "@xyflow/react";
import { Box, Stack, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { useQueryClient } from "@tanstack/react-query";
import SvgColor from "src/components/svg-color";
import logger from "src/utils/logger";
import { deleteConnectionApi } from "src/api/agent-playground/agent-playground";
import {
  useAgentPlaygroundStore,
  useAgentPlaygroundStoreShallow,
} from "../../store";
import { EDGE_STATE, NODE_X_OFFSET } from "../../utils/constants";
import { useSaveDraftContext } from "../saveDraftContext";
import NodeSelectionPopper from "../../components/NodeSelectionPopper";
import useCanEditAgent from "../../hooks/useCanEditAgent";
import { useWorkflowRunStoreShallow } from "../../store";
import useAddNodeOptimistic from "../hooks/useAddNodeOptimistic";
import "../agent-graph.css";

// Pure function — extracted outside the component to avoid recreating on every render.
// Computes SVG polyline points for the open arrow (chevron >) at the edge target.
function getArrowPolyline(targetX, targetY, targetPosition) {
  const size = 6;
  const offset = -1;
  const arrowLength = size * 2;

  switch (targetPosition) {
    case "left":
      return `${targetX + offset - size * 2},${targetY - size} ${targetX + offset},${targetY} ${targetX + offset - size * 2},${targetY + size}`;
    case "right":
      return `${targetX - offset + size * 2},${targetY - size} ${targetX - offset},${targetY} ${targetX - offset + size * 2},${targetY + size}`;
    case "top":
      return `${targetX - size},${targetY + offset - size * 2} ${targetX},${targetY + offset} ${targetX + size},${targetY + offset - size * 2}`;
    case "bottom":
      return `${targetX - size},${targetY - offset + size * 2} ${targetX},${targetY - offset} ${targetX + size},${targetY - offset + size * 2}`;
    default:
      return `${targetX - arrowLength},${targetY - size} ${targetX - 2},${targetY} ${targetX - arrowLength},${targetY + size}`;
  }
}

export default function AnimatedEdge({
  id,
  source,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}) {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const preview = data?.preview;
  const [hovered, setHovered] = useState(false);
  const [popperOpen, setPopperOpen] = useState(false);
  const hideTimeout = useRef(null);
  const addButtonRef = useRef(null);
  const { ensureDraft } = useSaveDraftContext();
  const { setCenter, getZoom, getNode } = useReactFlow();
  const isWorkflowRunning = useWorkflowRunStoreShallow((s) => s.isRunning);
  const { isReadOnly } = useCanEditAgent();

  const { addNode } = useAddNodeOptimistic();

  const { setGraphData, onEdgesChange } = useAgentPlaygroundStoreShallow(
    (state) =>
      preview
        ? {}
        : {
            setGraphData: state.setGraphData,
            onEdgesChange: state.onEdgesChange,
          },
  );

  const handleMouseEnter = useCallback(() => {
    if (hideTimeout.current) clearTimeout(hideTimeout.current);
    setHovered(true);
  }, []);

  const handleMouseLeave = useCallback(() => {
    if (hideTimeout.current) clearTimeout(hideTimeout.current);
    hideTimeout.current = setTimeout(() => {
      if (!popperOpen) setHovered(false);
    }, 300);
  }, [popperOpen]);

  useEffect(
    () => () => {
      if (hideTimeout.current) clearTimeout(hideTimeout.current);
    },
    [],
  );

  const { edgeExecutionStates } = useAgentPlaygroundStoreShallow((state) =>
    preview
      ? { edgeExecutionStates: {} }
      : { edgeExecutionStates: state.edgeExecutionStates },
  );

  const executionState = edgeExecutionStates[id] || EDGE_STATE.IDLE;

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
  });

  const isActive = executionState === EDGE_STATE.ACTIVE;
  const isCompleted = executionState === EDGE_STATE.COMPLETED;
  const isWaiting = executionState === EDGE_STATE.WAITING;
  const isIdle = executionState === EDGE_STATE.IDLE;
  const greenColor = theme.palette.green[500];
  const grayColor = theme.palette.grey[400];
  const hoverColor = theme.palette.grey[600];
  const idleColor = hovered && isIdle ? hoverColor : grayColor;
  const currentColor = isActive || isCompleted ? greenColor : idleColor;

  const handleDelete = useCallback(
    async (e) => {
      e.stopPropagation();
      if (isWorkflowRunning || isReadOnly) return;

      // Always apply optimistic deletion first
      const { edges: prevEdges, nodes } = useAgentPlaygroundStore.getState();
      onEdgesChange([{ type: "remove", id }]);

      const draftResult = await ensureDraft({ skipDirtyCheck: true });

      if (draftResult === false) {
        // Rollback
        setGraphData(nodes, prevEdges);
        return;
      }

      if (draftResult === "created") {
        // Deletion included in POST, IDs remapped. Done!
        return;
      }

      // Already a draft — fire individual DELETE
      const { currentAgent } = useAgentPlaygroundStore.getState();
      try {
        await deleteConnectionApi({
          graphId: currentAgent?.id,
          versionId: currentAgent?.version_id,
          connectionId: id,
        });
        // Refetch edge mappings now that the connection is removed on the backend
        queryClient.invalidateQueries({
          queryKey: ["agent-playground", "possible-edge-mappings"],
        });
      } catch (error) {
        logger.error("[AnimatedEdge] deleteConnectionApi failed", error);
        setGraphData(nodes, prevEdges);
        enqueueSnackbar("Failed to delete connection", { variant: "error" });
      }
    },
    [
      id,
      ensureDraft,
      onEdgesChange,
      setGraphData,
      isWorkflowRunning,
      isReadOnly,
      queryClient,
    ],
  );

  const handleAddClick = useCallback(
    (e) => {
      e.stopPropagation();
      if (isReadOnly) return;
      setPopperOpen(true);
    },
    [isReadOnly],
  );

  const handlePopperClose = useCallback(() => {
    setPopperOpen(false);
    setHovered(false);
  }, []);

  const handleNodeSelect = useCallback(
    (nodeType, nodeTemplateId, initialConfig) => {
      if (isWorkflowRunning || isReadOnly) return;

      const sourceNode = getNode(source);

      // Position the new node branching from the source
      const position = sourceNode
        ? {
            x: sourceNode.position.x + NODE_X_OFFSET,
            y: sourceNode.position.y,
          }
        : undefined;

      // One-to-many: add new node with edge from source, keep existing edge intact
      addNode({
        type: nodeType,
        position,
        sourceNodeId: source,
        node_template_id: nodeTemplateId,
        name: initialConfig?.name,
        config: initialConfig,
      });

      if (position) {
        setCenter(position.x, position.y, {
          duration: 800,
          zoom: getZoom(),
        });
      }

      setPopperOpen(false);
      setHovered(false);
    },
    [
      source,
      isWorkflowRunning,
      isReadOnly,
      getNode,
      getZoom,
      setCenter,
      addNode,
    ],
  );

  const showActions =
    hovered && !preview && !isActive && !isWorkflowRunning && !isReadOnly;

  // When nodes are close, shift action buttons slightly above the edge to avoid
  // overlapping the source node's output label.
  const edgeGap = Math.abs(targetX - sourceX);
  const actionOffsetY = edgeGap < 300 ? -16 : 0;

  return (
    <>
      {/* Invisible wider hit-area for hover detection */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={30}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      />

      {/* Foreground edge - overlay when active, waiting, or completed */}
      {isActive || isCompleted || isWaiting ? (
        <path
          d={edgePath}
          fill="none"
          stroke={isWaiting ? grayColor : greenColor}
          strokeWidth={2}
          strokeDasharray="8 6"
          strokeDashoffset={isActive || isWaiting ? 0 : undefined}
          style={
            isActive || isWaiting
              ? {
                  animation: "dash-flow 1.5s linear infinite",
                }
              : undefined
          }
        />
      ) : (
        <path
          d={edgePath}
          fill="none"
          stroke={idleColor}
          strokeWidth={2}
          strokeDasharray="8 6"
          style={{ transition: "stroke 0.2s ease" }}
        />
      )}

      {/* Open arrow marker (chevron) at the end */}
      <polyline
        points={getArrowPolyline(targetX, targetY, targetPosition)}
        fill="none"
        stroke={currentColor}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ transition: "stroke 0.2s ease" }}
      />

      {/* Action buttons at edge midpoint — shown on hover */}
      {showActions && (
        <EdgeLabelRenderer>
          <Box
            sx={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY + actionOffsetY}px)`,
              pointerEvents: "all",
              zIndex: 30,
              p: 0.5,
            }}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
          >
            <Stack direction="row" spacing={0.5}>
              {/* Add node button */}
              <Box
                ref={addButtonRef}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 20,
                  height: 20,
                  borderRadius: "4px",
                  bgcolor: "background.paper",
                  border: "1px solid",
                  borderColor: "blue.500",
                  cursor: "pointer",
                  "&:hover": {
                    bgcolor: (t) =>
                      t.palette.mode === "dark" ? "black.800" : "blue.50",
                  },
                }}
                onClick={handleAddClick}
              >
                <SvgColor
                  src="/assets/icons/ic_add.svg"
                  sx={{
                    width: 14,
                    height: 14,
                    bgcolor: "blue.500",
                  }}
                />
              </Box>

              {/* Delete edge button */}
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 20,
                  height: 20,
                  borderRadius: "4px",
                  bgcolor: "background.paper",
                  border: "1px solid",
                  borderColor: "red.500",
                  cursor: "pointer",
                  "&:hover": {
                    bgcolor: (t) =>
                      t.palette.mode === "dark" ? "black.800" : "red.50",
                  },
                }}
                onClick={handleDelete}
              >
                <SvgColor
                  src="/assets/icons/ic_delete.svg"
                  sx={{
                    width: 14,
                    height: 14,
                    color: "red.500",
                  }}
                />
              </Box>
            </Stack>
          </Box>
        </EdgeLabelRenderer>
      )}

      {/* Node selection popper for adding a branching node from source */}
      {!preview && (
        <NodeSelectionPopper
          open={popperOpen}
          anchorEl={addButtonRef.current}
          onClose={handlePopperClose}
          onNodeSelect={handleNodeSelect}
        />
      )}
    </>
  );
}

AnimatedEdge.propTypes = {
  id: PropTypes.string.isRequired,
  source: PropTypes.string.isRequired,
  sourceX: PropTypes.number.isRequired,
  sourceY: PropTypes.number.isRequired,
  targetX: PropTypes.number.isRequired,
  targetY: PropTypes.number.isRequired,
  sourcePosition: PropTypes.string.isRequired,
  targetPosition: PropTypes.string.isRequired,
  data: PropTypes.object,
};
