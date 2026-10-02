import { Box, Skeleton, Stack } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import { useReactFlow } from "@xyflow/react";
import { AGENT_NODE } from "../utils/constants";
import {
  useGetNodeTemplates,
  useGetReferenceableGraphs,
} from "src/api/agent-playground/agent-playground";
import { useAgentPlaygroundStoreShallow } from "../store";
import NodeCard from "../components/NodeCard";
import useAddNodeOptimistic from "./hooks/useAddNodeOptimistic";

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

  const { currentAgent } = useAgentPlaygroundStoreShallow((state) => ({
    currentAgent: state.currentAgent,
  }));
  const { data: referenceableGraphs = [] } = useGetReferenceableGraphs(
    currentAgent?.id,
  );

  const { data: templateNodes = [], isLoading } = useGetNodeTemplates();
  const nodesList = useMemo(
    () => [...templateNodes, AGENT_NODE],
    [templateNodes],
  );

  const handleNodeClick = useCallback(
    async (node) => {
      if (disabled) return;
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
    [addNode, disabled, setCenter, getZoom],
  );

  const handleDragStart = useCallback(
    (event, node) => {
      if (disabled) {
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
    [disabled],
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
              onClick={() => handleNodeClick(node)}
              onDragStart={(e) => handleDragStart(e, node)}
              draggable={!disabled}
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
    </Box>
  );
}

NodeSelectionPanel.propTypes = {
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  disabled: PropTypes.bool,
};
