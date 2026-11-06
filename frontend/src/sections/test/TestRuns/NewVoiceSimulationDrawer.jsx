import { Drawer, IconButton } from "@mui/material";
import React from "react";
import { useTestRunSdkStoreShallow } from "./state";
import SDkComponentVoiceTestRun from "./SDkComponentVoiceTestRun";
import Iconify from "src/components/iconify";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const NewVoiceSimulationDrawer = () => {
  const { sdkCodeOpen, setSdkCodeOpen } = useTestRunSdkStoreShallow((state) => {
    return {
      sdkCodeOpen: state.sdkCodeOpen,
      setSdkCodeOpen: state.setSdkCodeOpen,
    };
  });
  const covered = useDrawerCovered(sdkCodeOpen);
  return (
    <Drawer
      anchor="right"
      open={sdkCodeOpen}
      sx={coveredDrawerSx(covered)}
      onClose={() => {
        setSdkCodeOpen(false);
      }}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
          padding: 2,
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <IconButton
        onClick={() => {
          setSdkCodeOpen(false);
        }}
        sx={{ position: "absolute", top: 2, right: 2 }}
      >
        <Iconify icon="solar:close-linear" />
      </IconButton>

      <SDkComponentVoiceTestRun />
    </Drawer>
  );
};

export default NewVoiceSimulationDrawer;
