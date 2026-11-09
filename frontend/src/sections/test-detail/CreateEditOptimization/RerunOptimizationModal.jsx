import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
  Alert,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import CreateEditOptimizationForm from "./CreateEditOptimizationForm";
import PropTypes from "prop-types";

const RerunOptimizationModal = ({
  open,
  onClose,
  defaultValues,
  onSuccess,
}) => {
  return (
    <Dialog open={open} onClose={onClose} width="560px" fullWidth>
      <DialogTitle
        sx={{
          padding: 2,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography typography="m2" fontWeight="fontWeightMedium">
          Rerun Optimization
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
        <Alert severity="info" sx={{ marginBottom: 2 }}>
          Rerunning this optimization will create a new run
        </Alert>
        <Box sx={{ paddingTop: 0.5 }}>
          <CreateEditOptimizationForm
            onClose={onClose}
            defaultValues={defaultValues}
            onSuccess={onSuccess}
          />
        </Box>
      </DialogContent>
    </Dialog>
  );
};

RerunOptimizationModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  defaultValues: PropTypes.object,
  onSuccess: PropTypes.func,
};

export default RerunOptimizationModal;
