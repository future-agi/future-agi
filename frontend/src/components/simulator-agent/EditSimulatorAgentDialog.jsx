import React, { useState, useEffect } from "react";
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

const EditSimulatorAgentDialog = ({ open, onClose, agent, onEditSuccess }) => {
  const { enqueueSnackbar } = useSnackbar();
  const [isEditing, setIsEditing] = useState(false);

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

  // Initialize form data when agent changes
  useEffect(() => {
    if (agent) {
      setFormData({
        name: agent.name || "",
        prompt: agent.prompt || "",
        voiceProvider: agent.voice_provider || "",
        voiceName: agent.voice_name || "",
        interruptSensitivity: agent.interrupt_sensitivity || 0.5,
        conversationSpeed: agent.conversation_speed || 1.0,
        finishedSpeakingSensitivity: agent.finished_speaking_sensitivity || 0.5,
        model: agent.model || "",
        llmTemperature: agent.llm_temperature || 0.7,
        maxCallDurationInMinutes: agent.max_call_duration_in_minutes || 30,
        initialMessageDelay: agent.initial_message_delay || 0,
        initialMessage: agent.initial_message || "",
      });
    }
  }, [agent]);

  const handleClose = () => {
    if (!isEditing) {
      setErrors({});
      // Reset form data to original values
      if (agent) {
        setFormData({
          name: agent.name || "",
          prompt: agent.prompt || "",
          voiceProvider: agent.voice_provider || "",
          voiceName: agent.voice_name || "",
          interruptSensitivity: agent.interrupt_sensitivity || 0.5,
          conversationSpeed: agent.conversation_speed || 1.0,
          finishedSpeakingSensitivity: agent.finished_speaking_sensitivity || 0.5,
          model: agent.model || "",
          llmTemperature: agent.llm_temperature || 0.7,
          maxCallDurationInMinutes: agent.max_call_duration_in_minutes || 30,
          initialMessageDelay: agent.initial_message_delay || 0,
          initialMessage: agent.initial_message || "",
        });
      }
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

  const handleEdit = async () => {
    if (!agent?.id || !validateForm()) {
      return;
    }

    setIsEditing(true);

    try {
      await axios.put(endpoints.simulatorAgents.edit(agent.id), {
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

      enqueueSnackbar("Simulator agent updated successfully", {
        variant: "success",
      });

      onEditSuccess?.();
      handleClose();
    } catch (error) {
      // console.error("Error updating simulator agent:", error);
      enqueueSnackbar(error.message || "Failed to update simulator agent", {
        variant: "error",
      });
    } finally {
      setIsEditing(false);
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
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
        },
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
        <Typography variant="h6">Edit Simulator Agent</Typography>
        <IconButton onClick={handleClose} disabled={isEditing}>
          <Iconify icon="eva:close-fill" />
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
            disabled={isEditing}
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
            disabled={isEditing}
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
                disabled={isEditing}
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
                disabled={isEditing}
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
              disabled={isEditing}
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
              disabled={isEditing}
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
              disabled={isEditing}
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
                disabled={isEditing}
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
                  disabled={isEditing}
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
                disabled={isEditing}
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
                disabled={isEditing}
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
            disabled={isEditing}
            placeholder="Optional message to send when conversation starts"
          />
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 3 }}>
        <Button onClick={handleClose} color="inherit" disabled={isEditing}>
          Cancel
        </Button>
        <Button
          onClick={handleEdit}
          variant="contained"
          disabled={isEditing}
          startIcon={
            isEditing ? <CircularProgress size={16} color="inherit" /> : null
          }
        >
          {isEditing ? "Updating..." : "Update Agent"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

EditSimulatorAgentDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  agent: PropTypes.object,
  onEditSuccess: PropTypes.func,
};

export default EditSimulatorAgentDialog;
