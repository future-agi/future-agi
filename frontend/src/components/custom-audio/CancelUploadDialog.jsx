import React from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  IconButton,
  Typography,
} from "@mui/material";
import { Icon } from "@iconify/react";
import PropTypes from "prop-types";

const CancelUploadDialog = ({ open, onClose, onConfirmCancel }) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      sx={{
        zIndex: 9999,
        "& .MuiDialog-paper": {
          position: "absolute",
          top: "50%",
          left: "50%",
          minWidth: "28%",
          transform: "translate(-50%, -50%)",
          borderRadius: "8px",
          pt: 1,
        },
      }}
    >
      {/* Title & Close Button */}
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          p: 0,
          pl: 2,
        }}
      >
        <Typography fontWeight="700" fontSize="16px" color="text.primary">
          Are you sure you want to cancel the upload?
        </Typography>
        <IconButton aria-label="close-cancel-upload" onClick={onClose}>
          <Icon icon="material-symbols:close" color="black" />
        </IconButton>
      </DialogTitle>

      {/* Subtitle */}
      <DialogContent
        sx={{
          p: "0px",
        }}
      >
        <Typography
          fontSize="14px"
          color="text.secondary"
          sx={{
            pr: 4,
            pl: 2,
          }}
        >
          If you cancel the upload you will lose the update progress
        </Typography>
      </DialogContent>

      {/* Buttons */}
      <DialogActions sx={{ justifyContent: "flex-end", pb: 2 }}>
        <Button
          aria-label="continue-upload"
          variant="outlined"
          color="inherit"
          onClick={onClose}
          sx={{
            width: "90px",
            paddingX: 2,
            borderRadius: "8px",
            color: "text.disabled",
            fontSize: "12px",
          }}
        >
          No
        </Button>
        <Button
          aria-label="cancel-upload"
          variant="contained"
          sx={{
            minWidth: "90px",
            borderRadius: "8px",
            paddingX: 2,
            backgroundColor: "red.500",
            fontSize: "12px",
            "&:hover": {
              backgroundColor: "red.500",
            },
          }}
          onClick={onConfirmCancel}
        >
          Yes
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CancelUploadDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConfirmCancel: PropTypes.func, // Function to handle cancellation
};

export default CancelUploadDialog;
