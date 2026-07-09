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

const UnsupportedFileDialog = ({ open, onClose, onUpload }) => {
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
          Whoops! Not the right file type
        </Typography>
        <IconButton aria-label="close-dialogue" onClick={onClose}>
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
          Please upload as MP3, WAV, MPEG or other supported audio formats.
        </Typography>
      </DialogContent>

      {/* Buttons */}
      <DialogActions sx={{ justifyContent: "flex-end", pb: 2 }}>
        <Button
          aria-label="close-pop-up"
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
          Cancel
        </Button>
        <Button
          aria-label="continue-upload"
          variant="contained"
          sx={{
            minWidth: "90px",
            borderRadius: "8px",
            paddingX: 2,
            backgroundColor: "primary.main",
            fontSize: "12px",
            "&:hover": {
              backgroundColor: "primary.main",
            },
          }}
          onClick={onUpload}
        >
          Upload Audio
        </Button>
      </DialogActions>
    </Dialog>
  );
};

UnsupportedFileDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onUpload: PropTypes.func,
};

export default UnsupportedFileDialog;
