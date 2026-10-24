/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  Slider,
  Typography,
  FormControlLabel,
  Checkbox,
  Autocomplete,
} from "@mui/material";
import { useAvailableModels } from "../hooks/useAvailableModels";

const INITIAL = {
  name: "",
  description: "",
  source_model: "",
  shadow_model: "",
  shadow_provider: "",
  sample_rate: 0.1,
};

const CreateExperimentDialog = ({ open, onClose, onSubmit, isLoading }) => {
  const availableModels = useAvailableModels();
  const [form, setForm] = useState({ ...INITIAL });
  const [startPaused, setStartPaused] = useState(false);

  const set = (field) => (e) =>
    setForm((prev) => ({ ...prev, [field]: e.target.value }));

  const handleSubmit = () => {
    const payload = { ...form };
    if (startPaused) payload.status = "paused";
    onSubmit(payload);
  };

  const handleClose = () => {
    setForm({ ...INITIAL });
    setStartPaused(false);
    onClose();
  };

  const valid =
    form.name.trim() &&
    form.source_model.trim() &&
    form.shadow_model.trim() &&
    form.shadow_provider.trim();

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>Create Shadow Experiment</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} mt={1}>
          <TextField
            label="Name"
            value={form.name}
            onChange={set("name")}
            size="small"
            required
            inputProps={{ maxLength: 64 }}
            placeholder="e.g. gpt4o-vs-claude"
          />
          <TextField
            label="Description (optional)"
            value={form.description}
            onChange={set("description")}
            size="small"
            multiline
            rows={2}
            placeholder="What are you testing?"
          />

          <Autocomplete
            freeSolo
            options={availableModels}
            value={form.source_model}
            onChange={(_, val) =>
              setForm((prev) => ({ ...prev, source_model: val || "" }))
            }
            onInputChange={(_, val) =>
              setForm((prev) => ({ ...prev, source_model: val }))
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Production Model (source)"
                size="small"
                required
                helperText="The model currently serving real traffic"
                placeholder="e.g. gpt-4o"
              />
            )}
          />
          <Autocomplete
            freeSolo
            options={availableModels}
            value={form.shadow_model}
            onChange={(_, val) =>
              setForm((prev) => ({ ...prev, shadow_model: val || "" }))
            }
            onInputChange={(_, val) =>
              setForm((prev) => ({ ...prev, shadow_model: val }))
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Shadow Model"
                size="small"
                required
                placeholder="e.g. claude-sonnet-4"
              />
            )}
          />
          <TextField
            label="Shadow Provider"
            value={form.shadow_provider}
            onChange={set("shadow_provider")}
            size="small"
            required
            placeholder="e.g. anthropic"
          />

          <Stack spacing={1}>
            <Typography variant="body2" fontWeight={500}>
              Sample Rate: {Math.round(form.sample_rate * 100)}%
            </Typography>
            <Slider
              value={form.sample_rate}
              onChange={(_, val) =>
                setForm((prev) => ({ ...prev, sample_rate: val }))
              }
              min={0.01}
              max={1}
              step={0.01}
              valueLabelDisplay="auto"
              valueLabelFormat={(v) => `${Math.round(v * 100)}%`}
            />
            <Typography variant="caption" color="text.secondary">
              Percentage of production traffic to mirror to the shadow model.
              Higher = more data but more shadow cost.
            </Typography>
          </Stack>

          <FormControlLabel
            control={
              <Checkbox
                checked={startPaused}
                onChange={(e) => setStartPaused(e.target.checked)}
                size="small"
              />
            }
            label="Create as paused (don't start immediately)"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={!valid || isLoading}
        >
          {startPaused ? "Create (Paused)" : "Create & Start"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CreateExperimentDialog;
