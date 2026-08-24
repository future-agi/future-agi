/* eslint-disable react/prop-types */
import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  MenuItem,
  FormControlLabel,
  Switch,
  IconButton,
  InputAdornment,
  Autocomplete,
  Chip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useAvailableModels } from "../hooks/useAvailableModels";

const PROVIDER_OPTIONS = [
  { value: "openai", label: "OpenAI", baseUrl: "https://api.openai.com" },
  {
    value: "anthropic",
    label: "Anthropic",
    baseUrl: "https://api.anthropic.com",
  },
  { value: "azure", label: "Azure OpenAI", baseUrl: "" },
  { value: "bedrock", label: "AWS Bedrock", baseUrl: "" },
  { value: "vertex", label: "Google Vertex", baseUrl: "" },
  { value: "groq", label: "Groq", baseUrl: "https://api.groq.com/openai" },
  { value: "cohere", label: "Cohere", baseUrl: "https://api.cohere.ai/compatibility/v1" },
  { value: "mistral", label: "Mistral AI", baseUrl: "https://api.mistral.ai" },
  {
    value: "together",
    label: "Together AI",
    baseUrl: "https://api.together.xyz",
  },
  { value: "perplexity", label: "Perplexity", baseUrl: "https://api.perplexity.ai" },
  { value: "deepseek", label: "DeepSeek", baseUrl: "https://api.deepseek.com" },
  {
    value: "fireworks",
    label: "Fireworks AI",
    baseUrl: "https://api.fireworks.ai/inference",
  },
  { value: "cerebras", label: "Cerebras", baseUrl: "https://api.cerebras.ai" },
  { value: "deepinfra", label: "DeepInfra", baseUrl: "https://api.deepinfra.com" },
  { value: "huggingface", label: "Hugging Face", baseUrl: "https://api-inference.huggingface.co" },
  { value: "anyscale", label: "Anyscale", baseUrl: "https://api.endpoints.anyscale.com" },
  { value: "replicate", label: "Replicate", baseUrl: "https://api.replicate.com" },
  { value: "openrouter", label: "OpenRouter", baseUrl: "https://openrouter.ai/api" },
  { value: "xai", label: "xAI", baseUrl: "https://api.x.ai" },
  { value: "custom", label: "Custom", baseUrl: "" },
];

const ProviderConfigDialog = ({
  open,
  onClose,
  onSave,
  providerName,
  initialData,
}) => {
  const availableModels = useAvailableModels();
  const isEdit = Boolean(providerName);
  const [form, setForm] = useState({
    name: "",
    apiKey: "",
    baseUrl: "",
    timeout: 30,
    weight: 1.0,
    enabled: true,
    models: [],
  });
  const [showKey, setShowKey] = useState(false);

  useEffect(() => {
    if (open) {
      if (isEdit && initialData) {
        setForm({
          name: providerName,
          apiKey: initialData.api_key || initialData.apiKey || "",
          baseUrl: initialData.base_url || initialData.baseUrl || "",
          timeout: initialData.timeout || 30,
          weight: initialData.weight || 1.0,
          enabled: initialData.enabled !== false,
          models: initialData.models || [],
        });
      } else {
        setForm({
          name: "",
          apiKey: "",
          baseUrl: "",
          timeout: 30,
          weight: 1.0,
          enabled: true,
          models: [],
        });
      }
      setShowKey(false);
    }
  }, [open, isEdit, providerName, initialData]);

  const handleProviderChange = (e) => {
    const name = e.target.value;
    const opt = PROVIDER_OPTIONS.find((p) => p.value === name);
    setForm((prev) => ({
      ...prev,
      name,
      baseUrl: opt?.baseUrl || prev.baseUrl,
      models: [],
    }));
  };

  const handleSave = () => {
    onSave(form.name, {
      api_key: form.apiKey,
      base_url: form.baseUrl,
      timeout: form.timeout,
      weight: form.weight,
      enabled: form.enabled,
      models: form.models,
    });
    onClose();
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {isEdit ? `Edit Provider: ${providerName}` : "Add Provider"}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            select
            label="Provider"
            value={form.name}
            onChange={handleProviderChange}
            disabled={isEdit}
            fullWidth
          >
            {PROVIDER_OPTIONS.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>

          <TextField
            label="API Key"
            type={showKey ? "text" : "password"}
            value={form.apiKey}
            onChange={(e) => setForm((p) => ({ ...p, apiKey: e.target.value }))}
            fullWidth
            InputProps={{
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton size="small" onClick={() => setShowKey(!showKey)}>
                    <Iconify
                      icon={showKey ? "mdi:eye-off" : "mdi:eye"}
                      width={18}
                    />
                  </IconButton>
                </InputAdornment>
              ),
            }}
          />

          <TextField
            label="Base URL"
            value={form.baseUrl}
            onChange={(e) =>
              setForm((p) => ({ ...p, baseUrl: e.target.value }))
            }
            fullWidth
          />

          <Stack direction="row" spacing={2}>
            <TextField
              label="Timeout (s)"
              type="number"
              value={form.timeout}
              onChange={(e) =>
                setForm((p) => ({ ...p, timeout: Number(e.target.value) }))
              }
              sx={{ flex: 1 }}
            />
            <TextField
              label="Weight"
              type="number"
              inputProps={{ step: 0.1 }}
              value={form.weight}
              onChange={(e) =>
                setForm((p) => ({ ...p, weight: Number(e.target.value) }))
              }
              sx={{ flex: 1 }}
            />
          </Stack>

          <FormControlLabel
            control={
              <Switch
                checked={form.enabled}
                onChange={(e) =>
                  setForm((p) => ({ ...p, enabled: e.target.checked }))
                }
              />
            }
            label="Enabled"
          />

          <Autocomplete
            multiple
            freeSolo
            options={availableModels}
            value={form.models}
            onChange={(_, newValue) =>
              setForm((p) => ({ ...p, models: newValue }))
            }
            renderTags={(value, getTagProps) =>
              value.map((option, index) => (
                <Chip
                  variant="outlined"
                  label={option}
                  size="small"
                  {...getTagProps({ index })}
                  key={option}
                />
              ))
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Models"
                placeholder="Select or type model names"
                size="small"
              />
            )}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={!form.name || !form.apiKey}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ProviderConfigDialog;
