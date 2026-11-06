import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { SelectedNodeProvider } from "src/components/traceDetailDrawer/selectedNodeContext";
import TraceTree from "src/components/traceDetailDrawer/trace-tree";

import { columnOptions } from "../CompareDrawer2/CompareHelper";

const RunTraceTree = ({ observationSpans }) => {
  return (
    <SelectedNodeProvider>
      <Box
        sx={{
          height: "100%",
          padding: "14px",
          overflowY: "auto",
          overflowX: "hidden",
          "&::-webkit-scrollbar": {
            width: "5px !important",
          },
          "&::-webkit-scrollbar-thumb": {
            backgroundColor: "border.strong",
            borderRadius: "4px",
          },
          "&::-webkit-scrollbar-track": {
            backgroundColor: "transparent",
          },
        }}
      >
        <TraceTree
          treeData={observationSpans}
          columnOptionItems={columnOptions}
        />
      </Box>
    </SelectedNodeProvider>
  );
};

RunTraceTree.propTypes = {
  observationSpans: PropTypes.array,
};

export default RunTraceTree;
