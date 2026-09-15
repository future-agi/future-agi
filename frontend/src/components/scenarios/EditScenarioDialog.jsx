import React, { useState, useEffect } from "react";
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
  TextField,
  IconButton,
} from "@mui/material";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";

const EditScenarioDialog = ({ open, onClose, scenario, onEditSuccess }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [error, setError] = useState("");
  const [nameError, setNameError] = useState("");
  const [scenarioName, setScenarioName] = useState("");
  const [scenarioDescription, setScenarioDescription] = useState("");

  // Initialize form data when scenario changes
  useEffect(() => {
    if (scenario) {
      setScenarioName(scenario.name || "");
      setScenarioDescription(scenario.description || "");
    }
  }, [scenario]);

  const handleClose = () => {
    if (!isEditing) {
      setError("");
      setNameError("");
      // Reset form data
      setScenarioName(scenario?.name || "");
      setScenarioDescription(scenario?.description || "");
      onClose();
    }
  };

  const handleEdit = async () => {
    if (!scenario?.id || !scenarioName.trim()) return;

    setIsEditing(true);
    setError("");
    setNameError("");

    try {
      const payload = {
        name: scenarioName.trim(),
        description: scenarioDescription.trim(),
      };

      await axios.put(endpoints.scenarios.edit(scenario.id), payload);

      onEditSuccess?.();
      handleClose();
    } catch (err) {
      // Map backend field-level validation errors.
      const details = err?.details;
      if (details?.name) {
        setNameError(
          Array.isArray(details.name) ? details.name[0] : details.name,
        );
      } else {
        setError(
          err?.message ||
            err?.error ||
            "Failed to update scenario. Please try again.",
        );
      }
    } finally {
      setIsEditing(false);
    }
  };

  const hasChanges =
    scenarioName.trim() !== (scenario?.name || "") ||
    scenarioDescription.trim() !== (scenario?.description || "");

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          width: "500px",
        },
      }}
    >
      <DialogTitle>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Typography variant="h6" fontWeight="fontWeightSemiBold">
            Edit Scenario
          </Typography>
          <IconButton onClick={handleClose} size="small" disabled={isEditing}>
            <Iconify icon="eva:close-fill" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent>
        <Box sx={{ pt: 2 }}>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <TextField
              label="Scenario Name"
              placeholder="Enter scenario name"
              value={scenarioName}
              onChange={(e) => {
                setScenarioName(e.target.value);
                if (nameError) setNameError("");
              }}
              size="medium"
              required
              fullWidth
              disabled={isEditing}
              error={
                !!nameError || (!scenarioName.trim() && scenarioName.length > 0)
              }
              helperText={
                nameError ||
                (!scenarioName.trim() && scenarioName.length > 0
                  ? "Scenario name is required"
                  : "")
              }
            />

            <TextField
              label="Description (Optional)"
              placeholder="Enter scenario description"
              value={scenarioDescription}
              onChange={(e) => setScenarioDescription(e.target.value)}
              size="medium"
              fullWidth
              multiline
              rows={3}
              disabled={isEditing}
            />
          </Box>

          {error && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error}
            </Alert>
          )}
        </Box>
      </DialogContent>

      <DialogActions sx={{ p: 3, pt: 1 }}>
        <Button onClick={handleClose} color="inherit" disabled={isEditing}>
          Cancel
        </Button>
        <Button
          onClick={handleEdit}
          variant="contained"
          disabled={!scenarioName.trim() || !hasChanges || isEditing}
          startIcon={
            isEditing ? <CircularProgress size={16} color="inherit" /> : null
          }
        >
          {isEditing ? "Saving..." : "Save Changes"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

EditScenarioDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  scenario: PropTypes.object,
  onEditSuccess: PropTypes.func,
};

export default EditScenarioDialog;
