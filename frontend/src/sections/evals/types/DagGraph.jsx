// W7 A2 (W7-3): the decision tree as a read-only graph (@xyflow/react), laid
// out by depth from the first question. The node being edited and the last
// Test's path are highlighted; the outline cards are where it is edited.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { Background, ReactFlow, ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { graphOf } from "./dagEditorModel";

const graphSx = {
  height: 300,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  "& .react-flow__node": {
    fontSize: "11px",
    width: 200,
    padding: "6px 8px",
    borderRadius: "6px",
  },
  "& .react-flow__node.dag-leaf": { borderStyle: "dashed" },
  "& .react-flow__node.dag-on-path": {
    borderColor: "primary.main",
    borderWidth: 2,
    fontWeight: 600,
  },
  "& .react-flow__node.dag-selected": { bgcolor: "action.selected" },
};

export default function DagGraph({ dag, selected = null, path = null }) {
  const { nodes, edges } = useMemo(() => {
    const highlight = new Set(
      path
        ? [...path.steps.map((s) => s.node_id), path.leafId].filter(Boolean)
        : [],
    );
    const pathEdges = new Set(
      path ? path.steps.map((s) => `${s.node_id}:${s.answer}`) : [],
    );
    return graphOf(dag, { highlight, selected, pathEdges });
  }, [dag, selected, path]);
  return (
    <Box sx={graphSx} data-testid="dag-graph">
      <ReactFlowProvider>
        <ReactFlow
          // Refit when nodes come or go (fitView runs on mount).
          key={nodes.map((n) => n.id).join("|")}
          nodes={nodes}
          edges={edges}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} size={1} />
        </ReactFlow>
      </ReactFlowProvider>
    </Box>
  );
}

DagGraph.propTypes = {
  dag: PropTypes.object.isRequired,
  selected: PropTypes.string,
  path: PropTypes.shape({
    steps: PropTypes.array,
    leafId: PropTypes.string,
  }),
};
