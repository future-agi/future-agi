import { Box, Drawer, Stack } from "@mui/material";
import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import PlaygroundHeader from "./PlaygroundHeader";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import TopEvaluateSection from "./TopEvaluationSection/TopEvaluateSection";
import BottomEvaluationSection from "./BottomEvaluationSection/BottomEvaluationSection";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const EvalPlaygroundChild = ({ evaluation, onClose }) => {
  const tableRef = useRef(null);
  const [selectedData, setSelectedData] = useState({ model: null });
  const [initialLeftWidth, setInitialLeftWidth] = useState(50);
  const refreshGrid = () => {
    if (tableRef.current?.api) {
      tableRef.current.api.refreshServerSide({ force: true });
    }
  };

  return (
    <Stack
      sx={{ width: "100%", height: "100%", padding: 2 }}
      direction="column"
      gap={2}
    >
      <PlaygroundHeader evaluationName={evaluation?.name} onClose={onClose} />
      <Box sx={{ flex: 1 }}>
        <ResizablePanels
          leftPanel={
            <TopEvaluateSection
              evaluation={evaluation}
              refreshGrid={refreshGrid}
              setSelectedData={setSelectedData}
            />
          }
          rightPanel={
            <BottomEvaluationSection
              tableRef={tableRef}
              evaluation={evaluation}
              selectedData={selectedData}
              setInitialLeftWidth={setInitialLeftWidth}
            />
          }
          orientation="vertical"
          initialLeftWidth={initialLeftWidth}
          minLeftWidth={30}
          maxLeftWidth={70}
          showIcon
        />
      </Box>
    </Stack>
  );
};

EvalPlaygroundChild.propTypes = {
  evaluation: PropTypes.object,
  onClose: PropTypes.func,
};

const EvalPlayground = ({ open, onClose, evaluation }) => {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={coveredDrawerSx(covered)}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          width: "85vw",
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <EvalPlaygroundChild evaluation={evaluation} onClose={onClose} />
    </Drawer>
  );
};

EvalPlayground.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  evaluation: PropTypes.object,
};

export default EvalPlayground;
