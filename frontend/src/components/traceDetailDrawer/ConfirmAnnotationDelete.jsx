import { LoadingButton } from "@mui/lab";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "../iconify";

const ConfirmAnnotationDelete = ({ open, onClose, onConfirm, loading }) => {
  return (
    <Dialog open={open} onClose={onClose} PaperProps={{ sx: { width: 560 } }}>
      <DialogTitle
        sx={{
          p: 2,
          pb: 0,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        Delete Label
        <IconButton onClick={onClose}>
          <Iconify icon="solar:close-linear" width={20} />
        </IconButton>
      </DialogTitle>
      <DialogContent sx={{ px: 2 }}>
        <Typography variant="s1" color="text.secondary">
          This label has been used for an existing annotation. Deleting this
          label will also delete the annotations applied. Are you sure you want
          to proceed?
        </Typography>
      </DialogContent>
      <DialogActions sx={{ p: 2 }}>
        <Button
          variant="outlined"
          size="small"
          onClick={onClose}
          sx={{ lineHeight: 1.5 }}
        >
          Cancel
        </Button>
        <LoadingButton
          aria-label="Confirm-delete-annotation"
          variant="contained"
          onClick={onConfirm}
          color="error"
          loading={loading}
          sx={{ lineHeight: 1.5 }}
        >
          Delete
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

ConfirmAnnotationDelete.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
  loading: PropTypes.bool,
};

export default ConfirmAnnotationDelete;
