import { Icon } from "@iconify/react";
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

const DeleteEvalConfirm = ({
  open,
  onClose,
  onConfirm,
  onConfirmWithColumn,
  isLoading,
}) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-description"
      PaperProps={{
        sx: {
          minWidth: "560px",
        },
      }}
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
          <Typography fontSize={16} fontWeight={600} color={"text.primary"}>
            Are you sure you want delete this evaluation
          </Typography>
          <IconButton onClick={onClose} sx={{ p: 0, color: "text.primary" }}>
            <Icon icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogActions sx={{ paddingY: 2 }}>
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
          Delete Evaluation only
        </LoadingButton>
        <LoadingButton
          loading={isLoading}
          onClick={onConfirmWithColumn}
          variant="contained"
          autoFocus
          color="error"
        >
          Remove evaluation and result
        </LoadingButton>
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
};

export default DeleteEvalConfirm;
