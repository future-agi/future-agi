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
import React, { useEffect } from "react";
import Iconify from "src/components/iconify";

const ConfirmDowngrade = ({ open, onClose, onConfirm, isLoading }) => {
  useEffect(() => {
    // console.log("open : ", open)
  }, [open]);

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
          <Typography variant="h6">
            Are you sure you want to downgrade to free?
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="mdi:close" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onClose} variant="outlined">
          Keep my current plan
        </Button>

        <LoadingButton
          loading={isLoading}
          onClick={onConfirm}
          variant="outlined"
          autoFocus
          color="error"
        >
          Downgrade Plan
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

ConfirmDowngrade.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
  isLoading: PropTypes.bool,
  // count: PropTypes.number,
};

export default ConfirmDowngrade;
