import React from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  IconButton,
  Box,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";

const ConfirmDeleteDatasets = ({ open, onClose, onConfirm, isLoading }) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-description"
    >
      <DialogTitle
        sx={{
          gap: "10px",
          display: "flex",
          flexDirection: "column",
          padding: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Iconify
              icon="solar:trash-bin-trash-bold"
              sx={{ color: "text.primary" }}
            />
            <Typography variant="h6">Delete Experiments</Typography>
          </Box>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        <Typography fontSize={16} color="text.secondary">
          Are you sure you want to delete all selected Experiments?
        </Typography>
      </DialogContent>
      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onClose} variant="outlined">
          Cancel
        </Button>
        <LoadingButton
          loading={isLoading}
          onClick={onConfirm}
          variant="contained"
          autoFocus
          color="error"
        >
          Delete
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

ConfirmDeleteDatasets.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
  isLoading: PropTypes.bool,
};

export default ConfirmDeleteDatasets;
