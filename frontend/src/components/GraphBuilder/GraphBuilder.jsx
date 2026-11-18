import React, { useCallback, useEffect } from "react";
import { Box } from "@mui/material";

import "@xyflow/react/dist/style.css";

import { useGraphStore } from "./store/graphStore";

import GraphBuilderLeftBar from "./GraphBuilderLeftBar";
import PropTypes from "prop-types";
import GraphView from "./GraphView";
import NodeEdgeConfigureForm from "./ConfigForms/NodeEdgeConfigureForm";
import { ValidateAndTransformGraphSchema } from "./validation";
import { validateGraphConnectivity } from "./connectivity";
import DisconnectedNodesToast from "./DisconnectedNodesToast";
import { useSnackbar } from "src/components/snackbar";
import { dagreTransformAndLayout } from "./common";
import { ReactFlowProvider } from "@xyflow/react";

const GraphBuilder = ({ value, onChange, saveLoading, agentType }) => {
  const { nodes, edges, onNodesChange, onEdgesChange, onConnect, resetGraph } =
    useGraphStore();

  const { enqueueSnackbar } = useSnackbar();

  useEffect(() => {
    if (!value?.nodes || !value?.edges) return;
    const { nodes: newNodes, edges: newEdges } = dagreTransformAndLayout(
      value?.nodes,
      value?.edges,
      { editMode: true },
    );
    resetGraph(newNodes, newEdges);
  }, [value, resetGraph]);

  const onDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onSave = useCallback(() => {
    const currentNodes = useGraphStore.getState().nodes;
    const currentEdges = useGraphStore.getState().edges;

    const validatedGraph = ValidateAndTransformGraphSchema().safeParse({
      nodes: currentNodes,
      edges: currentEdges,
    });

    if (!validatedGraph.success) {
      const messageString = validatedGraph.error?.errors
        ?.map((error) => error.message)
        .join(", ");
      enqueueSnackbar(messageString, { variant: "error" });
      return;
    }

    const { orphanNames, orphanIds, noStartNode } = validateGraphConnectivity(
      currentNodes,
      currentEdges,
    );
    useGraphStore.getState().setOrphanHighlights(orphanIds);
    if (noStartNode) {
      enqueueSnackbar("Graph has no start node. Add one before saving.", {
        variant: "error",
      });
      return;
    }
    if (orphanIds.length > 0) {
      enqueueSnackbar(<DisconnectedNodesToast names={orphanNames} />, {
        variant: "error",
      });
      return;
    }

    onChange({
      ...value,
      nodes: validatedGraph.data.nodes,
      edges: validatedGraph.data.edges,
    });
  }, [value, onChange, enqueueSnackbar]);

  useEffect(() => {
    return () => {
      resetGraph();
    };
  }, [resetGraph]);
  return (
    <>
      <GraphBuilderLeftBar
        agentType={agentType}
        onSave={onSave}
        saveLoading={saveLoading}
      />
      <Box sx={{ flex: 1, position: "relative" }}>
        <ReactFlowProvider>
          <GraphView
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onDragOver={onDragOver}
            editMode={true}
          />
        </ReactFlowProvider>
      </Box>
      <NodeEdgeConfigureForm />
    </>
  );
};

GraphBuilder.propTypes = {
  value: PropTypes.any,
  onChange: PropTypes.func,
  onClose: PropTypes.func,
  saveLoading: PropTypes.bool,
  agentType: PropTypes.string,
};

export default GraphBuilder;
