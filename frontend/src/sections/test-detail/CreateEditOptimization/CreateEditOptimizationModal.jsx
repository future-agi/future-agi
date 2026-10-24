import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import React from "react";
import CreateEditOptimizationForm from "./CreateEditOptimizationForm";
import Iconify from "../../../components/iconify";
import { useFixMyAgentDrawerStoreShallow } from "../FixMyAgentDrawer/state";
import { FixMyAgentDrawerSections } from "../FixMyAgentDrawer/common";
import { useTestDetail } from "../context/TestDetailContext";

const CreateEditOptimizationModal = () => {
  const {
    setCreateEditOptimizationOpen,
    createEditOptimizationOpen,
    setOpenSection,
  } = useFixMyAgentDrawerStoreShallow((state) => ({
    setCreateEditOptimizationOpen: state.setCreateEditOptimizationOpen,
    createEditOptimizationOpen: state.createEditOptimizationOpen,
    setOpenSection: state.setOpenSection,
  }));
  const onClose = () => {
    setCreateEditOptimizationOpen(false);
  };

  const { refreshOptimizationGrid } = useTestDetail();

  return (
    <Dialog
      open={createEditOptimizationOpen}
      onClose={onClose}
      width="560px"
      fullWidth
    >
      <DialogTitle
        sx={{
          padding: 2,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography typography="m2" fontWeight="fontWeightMedium">
          Choose optimization type
        </Typography>
        <IconButton
          onClick={onClose}
          size="small"
          sx={{
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>
      <DialogContent sx={{ padding: 2 }}>
        <Box sx={{ paddingTop: 0.5 }}>
          <CreateEditOptimizationForm
            onClose={onClose}
            onSuccess={(data) => {
              const optimizationId = data?.data?.id;
              setOpenSection({
                id: optimizationId,
                section: FixMyAgentDrawerSections.OPTIMIZE,
              });
              refreshOptimizationGrid();
            }}
          />
        </Box>
      </DialogContent>
    </Dialog>
  );
};

export default CreateEditOptimizationModal;
