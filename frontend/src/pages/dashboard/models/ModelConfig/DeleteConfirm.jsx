import React from "react";
import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";

const DeleteConfirm = ({ open, onClose, onDeleteClick, loading }) => {
  return (
    <Dialog open={open} onClose={onClose}>
      <DialogTitle>Delete Model</DialogTitle>
      <DialogContent>Are you sure you want to delete this model.</DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <LoadingButton
          loading={loading}
          variant="contained"
          color="error"
          onClick={() => {
            onDeleteClick();
            onClose();
          }}
        >
          Delete
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

DeleteConfirm.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onDeleteClick: PropTypes.func,
  loading: PropTypes.bool,
};

export default DeleteConfirm;
