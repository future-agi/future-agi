import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
  IconButton,
  TextField,
} from "@mui/material";

import { GatewayIcon } from "../shell/gateway-icons";

// The dialog is named by its title and described by its message, so a screen
// reader announces what the confirmation does when focus moves into it.
// `showClose` adds a Close button at the top right: it dismisses like Cancel.
const ConfirmDialog = ({
  open,
  onClose,
  onConfirm,
  onExited,
  title = "Confirm Action",
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  confirmColor = "error",
  typeToConfirm,
  isLoading = false,
  showClose = false,
}) => {
  const [confirmText, setConfirmText] = useState("");
  const messageId = `${useId()}-message`;

  const needsTyping = Boolean(typeToConfirm);
  const canConfirm = needsTyping ? confirmText === typeToConfirm : true;

  const handleClose = () => {
    if (isLoading) return;
    setConfirmText("");
    onClose();
  };

  const handleConfirm = () => {
    if (isLoading || !canConfirm) return;
    onConfirm();
    setConfirmText("");
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      disableEscapeKeyDown={isLoading}
      TransitionProps={{ onExited }}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
      aria-describedby={message ? messageId : undefined}
    >
      {/* MUI names the dialog by this title; the Close button sits outside
          it so the name stays the title alone. */}
      <DialogTitle sx={showClose ? { pr: 7 } : undefined}>{title}</DialogTitle>
      {showClose && (
        <IconButton
          aria-label="Close"
          size="small"
          onClick={handleClose}
          disabled={isLoading}
          sx={{ position: "absolute", top: 14, right: 14 }}
        >
          <GatewayIcon name="close" size={16} />
        </IconButton>
      )}
      <DialogContent>
        {message && (
          <DialogContentText id={messageId} mb={needsTyping ? 2 : 0}>
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
          {cancelLabel}
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
  onExited: PropTypes.func,
  title: PropTypes.string,
  message: PropTypes.string,
  confirmLabel: PropTypes.string,
  cancelLabel: PropTypes.string,
  confirmColor: PropTypes.string,
  typeToConfirm: PropTypes.string,
  isLoading: PropTypes.bool,
  showClose: PropTypes.bool,
};

export default ConfirmDialog;
