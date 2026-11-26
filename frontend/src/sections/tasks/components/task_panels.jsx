import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Tab, Tabs, useMediaQuery } from "@mui/material";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";

// Task-only pane hiding. The shared splitter's API/visuals stay unchanged;
// neither editor is reparented or unmounted at a breakpoint or tab switch.
export default function TaskPanels({ leftPanel, rightPanel }) {
  const narrow = useMediaQuery("(max-width:1100px)");
  const [pane, setPane] = useState("left");
  const panel = (side, children) => (
    <Box
      id={`task-${side === "left" ? "settings" : "row"}-panel`}
      role={narrow ? "tabpanel" : undefined}
      aria-labelledby={
        narrow ? `task-${side === "left" ? "settings" : "row"}-tab` : undefined
      }
      hidden={narrow && pane !== side}
      sx={{ height: "100%" }}
    >
      {children}
    </Box>
  );
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minWidth: 0,
      }}
    >
      <Box sx={{ px: 2, flexShrink: 0, display: narrow ? "block" : "none" }}>
        <Tabs
          value={pane}
          onChange={(_, next) => setPane(next)}
          aria-label="Task workspace"
        >
          <Tab
            value="left"
            id="task-settings-tab"
            aria-controls="task-settings-panel"
            label="Task settings"
          />
          <Tab
            value="right"
            id="task-row-tab"
            aria-controls="task-row-panel"
            label="Matching row"
          />
        </Tabs>
      </Box>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          ...(narrow && {
            "& .panels-container > .first-panel": {
              display: pane === "left" ? "block" : "none",
              width: "100% !important",
              minWidth: "0 !important",
            },
            "& .panels-container > .second-panel": {
              display: pane === "right" ? "block" : "none",
              width: "100% !important",
              minWidth: "0 !important",
            },
            "& .panels-container > .divider-handle": { display: "none" },
          }),
        }}
      >
        <ResizablePanels
          initialLeftWidth={55}
          minLeftWidth={35}
          maxLeftWidth={75}
          showIcon
          leftPanel={panel("left", leftPanel)}
          rightPanel={panel("right", rightPanel)}
        />
      </Box>
    </Box>
  );
}
TaskPanels.propTypes = {
  leftPanel: PropTypes.node,
  rightPanel: PropTypes.node,
};
