import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Box,
  Typography,
  Button,
  TextField,
  Stack,
  IconButton,
  CircularProgress,
  Slider,
  Grid,
} from "@mui/material";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { useSnackbar } from "src/components/snackbar";

const AddSimulatorAgentModal = ({ open, onClose, onCreateSuccess }) => {
  const { enqueueSnackbar } = useSnackbar();
  const [isCreating, setIsCreating] = useState(false);

  // Form state
  const [formData, setFormData] = useState({
    name: "",
    prompt: "",
    voiceProvider: "",
    voiceName: "",
    interruptSensitivity: 0.5,
    conversationSpeed: 1.0,
    finishedSpeakingSensitivity: 0.5,
    model: "",
    llmTemperature: 0.7,
    maxCallDurationInMinutes: 30,
    initialMessageDelay: 0,
    initialMessage: "",
  });

  const [errors, setErrors] = useState({});

  const handleClose = () => {
    if (!isCreating) {
      // Reset form
      setFormData({
        name: "",
        prompt: "",
        voiceProvider: "",
        voiceName: "",
        interruptSensitivity: 0.5,
        conversationSpeed: 1.0,
        finishedSpeakingSensitivity: 0.5,
        model: "",
        llmTemperature: 0.7,
        maxCallDurationInMinutes: 30,
        initialMessageDelay: 0,
        initialMessage: "",
      });
      setErrors({});
      onClose();
    }
  };

  const validateForm = () => {
    const newErrors = {};

    if (!formData.name.trim()) {
      newErrors.name = "Name is required";
    }
    if (!formData.prompt.trim()) {
      newErrors.prompt = "Prompt is required";
    }
    if (!formData.voiceProvider.trim()) {
      newErrors.voiceProvider = "Voice provider is required";
    }
    if (!formData.voiceName.trim()) {
      newErrors.voiceName = "Voice name is required";
    }
    if (!formData.model.trim()) {
      newErrors.model = "Model is required";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleCreate = async () => {
    if (!validateForm()) {
      return;
    }

    setIsCreating(true);

    try {
      await axios.post(endpoints.simulatorAgents.create, {
        name: formData.name,
        prompt: formData.prompt,
        voice_provider: formData.voiceProvider,
        voice_name: formData.voiceName,
        interrupt_sensitivity: formData.interruptSensitivity,
        conversation_speed: formData.conversationSpeed,
        finished_speaking_sensitivity: formData.finishedSpeakingSensitivity,
        model: formData.model,
        llm_temperature: formData.llmTemperature,
        max_call_duration_in_minutes: formData.maxCallDurationInMinutes,
        initial_message_delay: formData.initialMessageDelay,
        initial_message: formData.initialMessage,
      });

      enqueueSnackbar("Simulator agent created successfully", {
        variant: "success",
      });

      onCreateSuccess?.();
      handleClose();
    } catch (error) {
      // console.error("Error creating simulator agent:", error);
      enqueueSnackbar(error.message || "Failed to create simulator agent", {
        variant: "error",
      });
    } finally {
      setIsCreating(false);
    }
  };

  const handleInputChange = (field) => (event) => {
    setFormData((prev) => ({
      ...prev,
      [field]: event.target.value,
    }));
    // Clear error for this field
    if (errors[field]) {
      setErrors((prev) => ({ ...prev, [field]: "" }));
    }
  };

  const handleSliderChange = (field) => (_, value) => {
    setFormData((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: { maxWidth: 960, borderRadius: 2 },
      }}
    >
      <DialogTitle
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          pb: 2,
        }}
      >
        <Typography variant="h6">Add Simulator Agent</Typography>
        <IconButton onClick={handleClose} disabled={isCreating}>
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ pb: 3 }}>
        <Stack spacing={3}>
          {/* Basic Information */}
          <TextField
            fullWidth
            label="Name"
            value={formData.name}
            onChange={handleInputChange("name")}
            error={!!errors.name}
            helperText={errors.name}
            disabled={isCreating}
          />

          <TextField
            fullWidth
            label="Prompt"
            value={formData.prompt}
            onChange={handleInputChange("prompt")}
            multiline
            rows={4}
            error={!!errors.prompt}
            helperText={errors.prompt}
            disabled={isCreating}
          />

          {/* Voice Settings */}
          <Grid container spacing={2}>
            <Grid item xs={6}>
              <TextField
                fullWidth
                label="Voice Provider"
                value={formData.voiceProvider}
                onChange={handleInputChange("voiceProvider")}
                error={!!errors.voiceProvider}
                helperText={errors.voiceProvider}
                disabled={isCreating}
              />
            </Grid>
            <Grid item xs={6}>
              <TextField
                fullWidth
                label="Voice Name"
                value={formData.voiceName}
                onChange={handleInputChange("voiceName")}
                error={!!errors.voiceName}
                helperText={errors.voiceName}
                disabled={isCreating}
              />
            </Grid>
          </Grid>

          {/* Sensitivity Settings */}
          <Box>
            <Typography gutterBottom>
              Interrupt Sensitivity: {formData.interruptSensitivity}
            </Typography>
            <Slider
              value={formData.interruptSensitivity}
              onChange={handleSliderChange("interruptSensitivity")}
              min={0}
              max={1}
              step={0.1}
              marks
              disabled={isCreating}
            />
          </Box>

          <Box>
            <Typography gutterBottom>
              Conversation Speed: {formData.conversationSpeed}
            </Typography>
            <Slider
              value={formData.conversationSpeed}
              onChange={handleSliderChange("conversationSpeed")}
              min={0.1}
              max={3.0}
              step={0.1}
              marks
              disabled={isCreating}
            />
          </Box>

          <Box>
            <Typography gutterBottom>
              Finished Speaking Sensitivity:{" "}
              {formData.finishedSpeakingSensitivity}
            </Typography>
            <Slider
              value={formData.finishedSpeakingSensitivity}
              onChange={handleSliderChange("finishedSpeakingSensitivity")}
              min={0}
              max={1}
              step={0.1}
              marks
              disabled={isCreating}
            />
          </Box>

          {/* Model Settings */}
          <Grid container spacing={2}>
            <Grid item xs={6}>
              <TextField
                fullWidth
                label="Model"
                value={formData.model}
                onChange={handleInputChange("model")}
                error={!!errors.model}
                helperText={errors.model}
                disabled={isCreating}
              />
            </Grid>
            <Grid item xs={6}>
              <Box>
                <Typography gutterBottom>
                  LLM Temperature: {formData.llmTemperature}
                </Typography>
                <Slider
                  value={formData.llmTemperature}
                  onChange={handleSliderChange("llmTemperature")}
                  min={0}
                  max={2}
                  step={0.1}
                  marks
                  disabled={isCreating}
                />
              </Box>
            </Grid>
          </Grid>

          {/* Call Settings */}
          <Grid container spacing={2}>
            <Grid item xs={6}>
              <TextField
                fullWidth
                label="Max Call Duration (minutes)"
                type="number"
                value={formData.maxCallDurationInMinutes}
                onChange={handleInputChange("maxCallDurationInMinutes")}
                InputProps={{
                  inputProps: { min: 1, max: 180 },
                }}
                disabled={isCreating}
              />
            </Grid>
            <Grid item xs={6}>
              <TextField
                fullWidth
                label="Initial Message Delay (seconds)"
                type="number"
                value={formData.initialMessageDelay}
                onChange={handleInputChange("initialMessageDelay")}
                InputProps={{
                  inputProps: { min: 0, max: 60 },
                }}
                disabled={isCreating}
              />
            </Grid>
          </Grid>

          <TextField
            fullWidth
            label="Initial Message"
            value={formData.initialMessage}
            onChange={handleInputChange("initialMessage")}
            multiline
            rows={3}
            disabled={isCreating}
            placeholder="Optional message to send when conversation starts"
          />
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 3 }}>
        <Button onClick={handleClose} color="inherit" disabled={isCreating}>
          Cancel
        </Button>
        <Button
          onClick={handleCreate}
          variant="contained"
          disabled={isCreating}
          startIcon={
            isCreating ? <CircularProgress size={16} color="inherit" /> : null
          }
        >
          {isCreating ? "Creating..." : "Create Agent"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

AddSimulatorAgentModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onCreateSuccess: PropTypes.func,
};

export default AddSimulatorAgentModal;
