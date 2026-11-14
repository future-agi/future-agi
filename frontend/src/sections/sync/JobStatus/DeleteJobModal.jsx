import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
} from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";

const DeleteJobModal = ({ open, onClose, connectionId }) => {
  const queryClient = useQueryClient();

  const { isPending, mutate } = useMutation({
    mutationFn: () =>
      axios.delete(`${endpoints.connections.deleteConnection}${connectionId}/`),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["connection-count"],
        type: "all",
      });
      queryClient.invalidateQueries({
        queryKey: ["connection-list"],
        type: "all",
      });
      enqueueSnackbar({
        variant: "success",
        message: "Connection deleted successfully.",
      });
      onClose();
    },
  });

  return (
    <Dialog
      open={open}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 400 } }}
      onClose={onClose}
    >
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box sx={{ gap: 2, display: "flex", alignItems: "center" }}>
          <Iconify icon="solar:trash-bin-trash-bold" />
          Delete Job?
        </Box>
        <IconButton onClick={() => onClose()}>
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>
      <DialogContent>Do you want to delete this Job?</DialogContent>
      <DialogActions>
        <Button onClick={() => onClose()} variant="outlined">
          Cancel
        </Button>
        <LoadingButton
          onClick={() => mutate()}
          loading={isPending}
          variant="contained"
          color="error"
        >
          Delete
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

DeleteJobModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  connectionId: PropTypes.string,
};

export default DeleteJobModal;
