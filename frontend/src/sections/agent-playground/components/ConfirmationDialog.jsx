import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Stack,
  IconButton,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "../../../components/svg-color/svg-color";

/**
 * Reusable Confirmation Dialog Component
 * Can be used for various confirmation actions like delete, stop, etc.
 */
export const ConfirmationDialog = ({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmText = "Confirm",
  cancelText = "Cancel",
  confirmColor = "error",
  isLoading = false,
  disabled = false,
}) => {
  const handleClose = () => {
    if (!isLoading && !disabled) {
      onClose();
    }
  };

  const handleConfirm = () => {
    if (!isLoading && !disabled) {
      onConfirm();
    }
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: {
          maxWidth: 400,
          borderRadius: 1,
          // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
          boxShadow: "0px 4px 20px rgba(0, 0, 0, 0.15)",
          p: 2,
        },
      }}
    >
      <DialogTitle sx={{ p: 0 }}>
        <Stack
          direction={"row"}
          alignItems={"center"}
          gap={1}
          justifyContent={"space-between"}
        >
          <Typography
            typography="m3"
            fontWeight="fontWeightBold"
            color="text.primary"
          >
            {title}
          </Typography>
          <IconButton
            onClick={handleClose}
            disabled={isLoading || disabled}
            sx={{
              color: "text.primary",
            }}
            size="small"
          >
            <SvgColor
              sx={{
                height: "24px",
                width: "24px",
              }}
              src={"/assets/icons/ic_close.svg"}
            />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent
        sx={{
          p: 0,
        }}
      >
        <Typography typography="s1" color="text.secondary">
          {message}
        </Typography>
      </DialogContent>
      <DialogActions sx={{ p: 0, mt: 3 }}>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={handleClose}
          disabled={isLoading || disabled}
        >
          {cancelText}
        </Button>
        <LoadingButton
          size="small"
          variant="contained"
          color={confirmColor}
          onClick={handleConfirm}
          loading={isLoading}
          disabled={disabled}
        >
          {confirmText}
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

ConfirmationDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
  title: PropTypes.string.isRequired,
  message: PropTypes.string.isRequired,
  confirmText: PropTypes.string,
  cancelText: PropTypes.string,
  confirmColor: PropTypes.oneOf([
    "error",
    "primary",
    "secondary",
    "success",
    "warning",
    "info",
  ]),
  isLoading: PropTypes.bool,
  disabled: PropTypes.bool,
};

export default ConfirmationDialog;
