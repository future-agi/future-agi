import { Box, Skeleton, Stack } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import { useReactFlow } from "@xyflow/react";
import { AGENT_NODE, NODE_TYPES } from "../utils/constants";
import { useGetNodeTemplates } from "src/api/agent-playground/agent-playground";
import NodeCard from "../components/NodeCard";
import AgentNodeSetupDialog from "../components/AgentNodeSetupDialog";
import useAddNodeOptimistic from "./hooks/useAddNodeOptimistic";
import useAgentNodeInsertGuard from "../hooks/useAgentNodeInsertGuard";

const NodeCardSkeleton = () => (
  <Box sx={{ borderRadius: 1, padding: 0.5, width: "220px" }}>
    <Stack direction="row" spacing={1.5} alignItems="flex-start">
      <Skeleton
        variant="rounded"
        width={36}
        height={36}
        sx={{ flexShrink: 0 }}
      />
      <Stack sx={{ flex: 1 }} gap={0.5}>
        <Skeleton variant="text" width="60%" height={18} />
        <Skeleton variant="text" width="90%" height={16} />
      </Stack>
    </Stack>
  </Box>
);

export default function NodeSelectionPanel({ width, disabled = false }) {
  const { addNode } = useAddNodeOptimistic();
  const { setCenter, getZoom } = useReactFlow();
  const { guardNodeInsert, isAgentInsertReady, setupDialogProps } =
    useAgentNodeInsertGuard();

  // The Agent node is always listed (TH-4549). Whether it can be inserted
  // right now is decided per click by useAgentNodeInsertGuard.
  const { data: templateNodes = [], isLoading } = useGetNodeTemplates();
  const nodesList = useMemo(
    () => [...templateNodes, AGENT_NODE],
    [templateNodes],
  );

  const insertNode = useCallback(
    async (node) => {
      const result = await addNode({
        type: node.id,
        position: undefined,
        node_template_id: node.node_template_id,
      });
      if (result?.position) {
        setCenter(result.position.x + 300, result.position.y, {
          duration: 800,
          zoom: getZoom(),
        });
      }
    },
    [addNode, setCenter, getZoom],
  );

  const handleNodeClick = useCallback(
    (node) => {
      if (disabled) return;
      guardNodeInsert(node.id, () => insertNode(node));
    },
    [disabled, guardNodeInsert, insertNode],
  );

  // Dragging onto the canvas bypasses the click guard, so the Agent card is
  // only draggable while an eligible agent exists; otherwise clicking it
  // opens the setup dialog instead of inserting an unusable node.
  const isDraggable = useCallback(
    (node) => !disabled && (node.id !== NODE_TYPES.AGENT || isAgentInsertReady),
    [disabled, isAgentInsertReady],
  );

  const handleDragStart = useCallback(
    (event, node) => {
      if (!isDraggable(node)) {
        event.preventDefault();
        return;
      }
      event.dataTransfer.setData("application/reactflow", node.id);
      if (node.node_template_id) {
        event.dataTransfer.setData(
          "application/node-template-id",
          node.node_template_id,
        );
      }
      event.dataTransfer.effectAllowed = "move";
    },
    [isDraggable],
  );

  return (
    <Box
      sx={{
        width,
        height: "100%",
        backgroundColor: "background.paper",
        borderRight: "1px solid",
        borderColor: "divider",
        position: "absolute",
        left: 0,
        top: 0,
        bottom: 0,
        p: 2,
        overflowY: "auto",
        overflowX: "hidden",
        ...(disabled && {
          opacity: 0.5,
          pointerEvents: "none",
        }),
      }}
    >
      <Stack spacing={1}>
        {isLoading ? (
          <>
            <NodeCardSkeleton />
            <NodeCardSkeleton />
          </>
        ) : (
          nodesList.map((node) => (
            <Box
              key={node.id}
              data-testid={`sidebar-node-${node.id}`}
              onClick={() => handleNodeClick(node)}
              onDragStart={(e) => handleDragStart(e, node)}
              draggable={isDraggable(node)}
              sx={{
                borderRadius: 0.5,
                overflow: "hidden",
                cursor: disabled ? "not-allowed" : "pointer",
                "&:hover": {
                  backgroundColor: "action.hover",
                },
              }}
            >
              <NodeCard node={node} showExpandIcon={false} />
            </Box>
          ))
        )}
      </Stack>
      <AgentNodeSetupDialog {...setupDialogProps} />
    </Box>
  );
}

NodeSelectionPanel.propTypes = {
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  disabled: PropTypes.bool,
};
