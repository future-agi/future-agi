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

const PaymentSuccessModal = ({ open, onClose }) => {
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
            Your payment has been successful!
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogActions sx={{ padding: 2 }}>
        <Button onClick={onClose} variant="outlined">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

PaymentSuccessModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};

export default PaymentSuccessModal;
