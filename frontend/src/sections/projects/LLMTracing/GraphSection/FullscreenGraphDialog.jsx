// Shared fullscreen wrapper for the trace graphs. `renderGraph` runs for both
// the inline and dialog renders, so each caller can wrap its own provider
// (AgentGraph needs ReactFlowProvider, AgentPath doesn't).
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Dialog } from "@mui/material";

const FullscreenGraphDialog = ({ onNodeClick, renderGraph }) => {
  const [fsOpen, setFsOpen] = useState(false);

  return (
    <>
      {renderGraph?.({
        isFullscreen: false,
        onToggleFullscreen: () => setFsOpen(true),
        onNodeClick,
      })}
      <Dialog
        fullScreen
        open={fsOpen}
        onClose={() => setFsOpen(false)}
        PaperProps={{ sx: { borderRadius: 0, bgcolor: "background.paper" } }}
      >
        <Box sx={{ height: "100vh", width: "100%" }}>
          {renderGraph?.({
            isFullscreen: true,
            onToggleFullscreen: () => setFsOpen(false),
            onNodeClick: (node) => {
              onNodeClick?.(node);
              setFsOpen(false);
            },
          })}
        </Box>
      </Dialog>
    </>
  );
};

FullscreenGraphDialog.propTypes = {
  onNodeClick: PropTypes.func,
  renderGraph: PropTypes.func.isRequired,
};

export default FullscreenGraphDialog;
