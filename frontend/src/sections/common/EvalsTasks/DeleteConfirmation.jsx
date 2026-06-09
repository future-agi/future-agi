import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";

const DeleteConfirmation = ({
  open,
  onClose,
  onConfirm,
  selectedItems,
  isLoading,
}) => {
  const [deleteCount, setDeleteCount] = useState(0);

  useEffect(() => {
    if (open) {
      setDeleteCount(selectedItems?.length ?? 0);
    }
    return () => {
      setDeleteCount(0);
    };
  }, [open]);

  const confirmationMessage = `Are you sure you want to delete ${
    deleteCount > 1 ? `the selected ${deleteCount} tasks` : `this task`
  }? All evaluation results for ${
    deleteCount > 1 ? "them" : "it"
  } will also be deleted.`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="delete-dialog"
      fullWidth
      maxWidth="xs"
    >
      <DialogTitle
        id="delete-dialog"
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
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
            }}
          >
            <Iconify icon="solar:trash-bin-trash-bold" width={24} height={24} />
            <Typography color={"text.primary"} fontWeight={700} fontSize="18px">
              {`Delete Task${deleteCount > 1 ? "s" : ""}`}
            </Typography>
          </Box>
          <IconButton onClick={onClose}>
            <Iconify icon="mdi:close" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent>
        <Typography color="text.secondary">{confirmationMessage}</Typography>
      </DialogContent>

      <DialogActions>
        <Button variant="outlined" color="secondary" onClick={onClose}>
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          color="error"
          disabled={isLoading}
          loading={isLoading}
          onClick={onConfirm}
        >
          Delete
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

DeleteConfirmation.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
  selectedItems: PropTypes.array.isRequired,
  isLoading: PropTypes.bool,
};

export default DeleteConfirmation;
