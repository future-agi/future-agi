import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";

const DeleteEvalConfirm = ({
  open,
  onClose,
  onConfirm,
  onConfirmWithColumn,
  isLoading,
  hideDeleteColumn = false,
}) => {
  return (
    <Dialog
      open={open}
      fullWidth
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
          <Typography variant="h6">
            Are you sure you want delete this evaluation?
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onClose} variant="outlined">
          Cancel
        </Button>

        <LoadingButton
          loading={isLoading}
          onClick={onConfirm}
          variant="outlined"
          autoFocus
          color="error"
          sx={{
            border: "1px solid",
            borderColor: "error.main",
            "&:hover": {
              borderColor: "error.main",
              backgroundColor: "status.danger.bg",
            },
          }}
        >
          Delete Evaluation Only
        </LoadingButton>
        {!hideDeleteColumn && (
          <LoadingButton
            loading={isLoading}
            onClick={onConfirmWithColumn}
            variant="contained"
            autoFocus
            color="error"
          >
            Remove Evaluation and Column
          </LoadingButton>
        )}
      </DialogActions>
    </Dialog>
  );
};

DeleteEvalConfirm.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
  onConfirmWithColumn: PropTypes.func,
  isLoading: PropTypes.bool,
  hideDeleteColumn: PropTypes.bool,
};

export default DeleteEvalConfirm;
