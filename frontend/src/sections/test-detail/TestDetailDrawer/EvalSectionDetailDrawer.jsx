import { Drawer } from "@mui/material";
import React from "react";
import EvalDrawerSection from "./EvalSection";
import { useTestDetailSideDrawerStoreShallow } from "../states";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const EvalSectionDetailDrawer = () => {
  const { evalView, setEvalView } = useTestDetailSideDrawerStoreShallow(
    (s) => ({ evalView: s.evalView, setEvalView: s.setEvalView }),
  );
  const covered = useDrawerCovered(!!evalView);
  return (
    <Drawer
      open={!!evalView}
      sx={coveredDrawerSx(covered)}
      onClose={() => setEvalView(null)}
      anchor="right"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 10,
          borderRadius: "0px !important",
          backgroundColor: "background.paper",
          display: "flex",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: {
            backgroundColor: "transparent",
            borderRadius: "0px !important",
          },
        },
      }}
    >
      <EvalDrawerSection />
    </Drawer>
  );
};

export default EvalSectionDetailDrawer;
