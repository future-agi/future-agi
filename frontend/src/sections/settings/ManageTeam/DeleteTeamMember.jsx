import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import { LoadingButton } from "@mui/lab";

const DeleteTeamMember = ({
  selectedUser,
  onClose,
  onDeleteClick,
  isLoading,
}) => {
  return (
    <Dialog
      onClose={onClose}
      open={Boolean(selectedUser)}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 400 } }}
      fullWidth
    >
      <DialogTitle>Delete User</DialogTitle>
      <DialogContent>
        <Typography variant="body2">
          Are you sure you want to delete <b>{selectedUser?.email}</b>
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} variant="outlined">
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          color="error"
          size="medium"
          onClick={onDeleteClick}
          loading={isLoading}
        >
          Yes, Delete
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

DeleteTeamMember.propTypes = {
  selectedUser: PropTypes.object,
  onClose: PropTypes.func,
  onDeleteClick: PropTypes.func,
  isLoading: PropTypes.bool,
};

export default DeleteTeamMember;
