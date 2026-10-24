import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Typography,
  Button,
  CircularProgress,
  Box,
  Alert,
} from "@mui/material";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import { useSnackbar } from "src/components/snackbar";

const DeleteSimulatorAgentDialog = ({
  open,
  onClose,
  agent,
  onDeleteSuccess,
}) => {
  const { enqueueSnackbar } = useSnackbar();
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState("");

  const handleClose = () => {
    if (!isDeleting) {
      setError("");
      onClose();
    }
  };

  const handleDelete = async () => {
    if (!agent?.id) return;

    setIsDeleting(true);
    setError("");

    try {
      await axios.delete(endpoints.simulatorAgents.delete(agent.id));

      enqueueSnackbar("Simulator agent deleted successfully", {
        variant: "success",
      });

      onDeleteSuccess?.();
      handleClose();
    } catch (err) {
      // console.error("Error deleting simulator agent:", err);
      const errorMessage =
        err.response?.data?.error ||
        err.message ||
        "Failed to delete simulator agent. Please try again.";

      setError(errorMessage);
      enqueueSnackbar(errorMessage, { variant: "error" });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: { maxWidth: 400, borderRadius: 2 },
      }}
    >
      <DialogTitle>
        <Typography variant="h6">Delete Simulator Agent</Typography>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ mb: 2 }}>
          <Typography variant="body1" gutterBottom>
            Are you sure you want to delete this simulator agent?
          </Typography>
          {agent && (
            <Box
              sx={{
                mt: 2,
                p: 2,
                bgcolor: "background.default",
                borderRadius: 1,
              }}
            >
              <Typography variant="subtitle2" color="text.secondary">
                Agent Name:
              </Typography>
              <Typography variant="body2" fontWeight="medium">
                {agent.name}
              </Typography>
              {agent.model && (
                <>
                  <Typography
                    variant="subtitle2"
                    color="text.secondary"
                    sx={{ mt: 1 }}
                  >
                    Model:
                  </Typography>
                  <Typography variant="body2">{agent.model}</Typography>
                </>
              )}
            </Box>
          )}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            This action cannot be undone.
          </Typography>
        </Box>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
      </DialogContent>

      <DialogActions sx={{ p: 3, pt: 1 }}>
        <Button onClick={handleClose} color="inherit" disabled={isDeleting}>
          Cancel
        </Button>
        <Button
          onClick={handleDelete}
          variant="contained"
          color="error"
          disabled={isDeleting}
          startIcon={
            isDeleting ? <CircularProgress size={16} color="inherit" /> : null
          }
        >
          {isDeleting ? "Deleting..." : "Delete Agent"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

DeleteSimulatorAgentDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  agent: PropTypes.object,
  onDeleteSuccess: PropTypes.func,
};

export default DeleteSimulatorAgentDialog;
