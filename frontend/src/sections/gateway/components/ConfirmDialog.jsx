import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
  TextField,
} from "@mui/material";

const ConfirmDialog = ({
  open,
  onClose,
  onConfirm,
  title = "Confirm Action",
  message,
  confirmLabel = "Confirm",
  confirmColor = "error",
  typeToConfirm,
  isLoading = false,
}) => {
  const [confirmText, setConfirmText] = useState("");

  const needsTyping = Boolean(typeToConfirm);
  const canConfirm = needsTyping ? confirmText === typeToConfirm : true;

  const handleClose = () => {
    setConfirmText("");
    onClose();
  };

  const handleConfirm = () => {
    onConfirm();
    setConfirmText("");
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        {message && (
          <DialogContentText mb={needsTyping ? 2 : 0}>
            {message}
          </DialogContentText>
        )}
        {needsTyping && (
          <TextField
            fullWidth
            label={`Type "${typeToConfirm}" to confirm`}
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            autoFocus
            size="small"
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={isLoading}>
          Cancel
        </Button>
        <Button
          color={confirmColor}
          variant="contained"
          onClick={handleConfirm}
          disabled={!canConfirm || isLoading}
        >
          {isLoading ? "Processing..." : confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

ConfirmDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
  title: PropTypes.string,
  message: PropTypes.string,
  confirmLabel: PropTypes.string,
  confirmColor: PropTypes.string,
  typeToConfirm: PropTypes.string,
  isLoading: PropTypes.bool,
};

export default ConfirmDialog;
