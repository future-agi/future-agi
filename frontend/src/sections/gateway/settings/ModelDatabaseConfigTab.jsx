/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  TextField,
  Switch,
  FormControlLabel,
  Paper,
  Alert,
  IconButton,
  Button,
  Divider,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Autocomplete,
} from "@mui/material";
import { Icon } from "@iconify/react";
import { useAvailableModels } from "../hooks/useAvailableModels";

const CAPABILITY_FIELDS = [
  { key: "function_calling", label: "Function Calling" },
  { key: "parallel_tool_calls", label: "Parallel Tool Calls" },
  { key: "vision", label: "Vision" },
  { key: "audio_input", label: "Audio Input" },
  { key: "audio_output", label: "Audio Output" },
  { key: "pdf_input", label: "PDF Input" },
  { key: "streaming", label: "Streaming" },
  { key: "response_schema", label: "Response Schema" },
  { key: "system_messages", label: "System Messages" },
  { key: "prompt_caching", label: "Prompt Caching" },
  { key: "reasoning", label: "Reasoning" },
];

const ModelDatabaseConfigTab = ({ modelDatabase, onChange }) => {
  const availableModels = useAvailableModels();
  const config = modelDatabase || {};
  const overrides = config.overrides || {};
  const [newModelId, setNewModelId] = useState("");

  const updateOverrides = (newOverrides) => {
    onChange({ ...config, overrides: newOverrides });
  };

  const addModel = () => {
    const id = newModelId.trim();
    if (!id || overrides[id]) return;
    updateOverrides({ ...overrides, [id]: {} });
    setNewModelId("");
  };

  const removeModel = (modelId) => {
    const updated = { ...overrides };
    delete updated[modelId];
    updateOverrides(updated);
  };

  const updateModel = (modelId, field, value) => {
    const current = overrides[modelId] || {};
    updateOverrides({
      ...overrides,
      [modelId]: { ...current, [field]: value },
    });
  };

  const updatePricing = (modelId, field, value) => {
    const current = overrides[modelId] || {};
    const pricing = current.pricing || {};
    updateOverrides({
      ...overrides,
      [modelId]: {
        ...current,
        pricing: {
          ...pricing,
          [field]: value === "" ? undefined : Number(value),
        },
      },
    });
  };

  const updateCapability = (modelId, field, value) => {
    const current = overrides[modelId] || {};
    const capabilities = current.capabilities || {};
    updateOverrides({
      ...overrides,
      [modelId]: {
        ...current,
        capabilities: { ...capabilities, [field]: value },
      },
    });
  };

  const modelIds = Object.keys(overrides);

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="solar:database-linear" width={24} />
        <Typography variant="h6">Model Database Overrides</Typography>
      </Stack>

      <Alert severity="info" sx={{ mb: 2 }}>
        Override model metadata (token limits, pricing, capabilities) for this
        organization. Overrides are merged on top of the global model database.
      </Alert>

      {/* Add model */}
      <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Add Model Override
        </Typography>
        <Stack direction="row" spacing={1}>
          <Autocomplete
            freeSolo
            options={availableModels.filter((m) => !overrides[m])}
            inputValue={newModelId}
            onInputChange={(_, val) => setNewModelId(val)}
            onChange={(_, val) => {
              if (val) {
                setNewModelId(val);
              }
            }}
            renderInput={(params) => (
              <TextField
                {...params}
                size="small"
                placeholder="Model ID (e.g. gpt-4o, claude-sonnet-4-20250514)"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addModel();
                  }
                }}
              />
            )}
            sx={{ flex: 1 }}
          />
          <Button
            size="small"
            variant="outlined"
            onClick={addModel}
            disabled={!newModelId.trim() || !!overrides[newModelId.trim()]}
          >
            Add Override
          </Button>
        </Stack>
      </Paper>

      {/* Model override cards */}
      {modelIds.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No model overrides configured. Add a model above to customize its
          metadata for this organization.
        </Typography>
      )}

      {modelIds.map((modelId) => {
        const ov = overrides[modelId] || {};
        const pricing = ov.pricing || {};
        const capabilities = ov.capabilities || {};

        return (
          <Accordion key={modelId} defaultExpanded sx={{ mb: 1 }}>
            <AccordionSummary
              expandIcon={<Icon icon="solar:alt-arrow-down-linear" />}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ flex: 1 }}
              >
                <Icon icon="solar:box-linear" width={18} />
                <Typography variant="subtitle1" fontWeight="600">
                  {modelId}
                </Typography>
                <Box sx={{ flex: 1 }} />
                <IconButton
                  sx={{ color: "status.danger.fg" }}
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeModel(modelId);
                  }}
                >
                  <Icon icon="solar:trash-bin-trash-linear" width={18} />
                </IconButton>
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={2}>
                {/* Token Limits */}
                <Typography variant="body2" fontWeight={500}>
                  Token Limits
                </Typography>
                <Stack direction="row" spacing={2}>
                  <TextField
                    size="small"
                    type="number"
                    label="Max Input Tokens"
                    value={ov.max_input_tokens ?? ""}
                    onChange={(e) =>
                      updateModel(
                        modelId,
                        "max_input_tokens",
                        e.target.value === ""
                          ? undefined
                          : Number(e.target.value),
                      )
                    }
                    sx={{ width: 200 }}
                  />
                  <TextField
                    size="small"
                    type="number"
                    label="Max Output Tokens"
                    value={ov.max_output_tokens ?? ""}
                    onChange={(e) =>
                      updateModel(
                        modelId,
                        "max_output_tokens",
                        e.target.value === ""
                          ? undefined
                          : Number(e.target.value),
                      )
                    }
                    sx={{ width: 200 }}
                  />
                </Stack>

                <Divider />

                {/* Pricing */}
                <Typography variant="body2" fontWeight={500}>
                  Pricing (USD per token)
                </Typography>
                <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
                  <TextField
                    size="small"
                    type="number"
                    label="Input per Token"
                    value={pricing.input_per_token ?? ""}
                    onChange={(e) =>
                      updatePricing(modelId, "input_per_token", e.target.value)
                    }
                    inputProps={{ step: 0.000001 }}
                    sx={{ width: 180 }}
                  />
                  <TextField
                    size="small"
                    type="number"
                    label="Output per Token"
                    value={pricing.output_per_token ?? ""}
                    onChange={(e) =>
                      updatePricing(modelId, "output_per_token", e.target.value)
                    }
                    inputProps={{ step: 0.000001 }}
                    sx={{ width: 180 }}
                  />
                  <TextField
                    size="small"
                    type="number"
                    label="Cached Input per Token"
                    value={pricing.cached_input_per_token ?? ""}
                    onChange={(e) =>
                      updatePricing(
                        modelId,
                        "cached_input_per_token",
                        e.target.value,
                      )
                    }
                    inputProps={{ step: 0.000001 }}
                    sx={{ width: 180 }}
                  />
                  <TextField
                    size="small"
                    type="number"
                    label="Batch Input per Token"
                    value={pricing.batch_input_per_token ?? ""}
                    onChange={(e) =>
                      updatePricing(
                        modelId,
                        "batch_input_per_token",
                        e.target.value,
                      )
                    }
                    inputProps={{ step: 0.000001 }}
                    sx={{ width: 180 }}
                  />
                  <TextField
                    size="small"
                    type="number"
                    label="Batch Output per Token"
                    value={pricing.batch_output_per_token ?? ""}
                    onChange={(e) =>
                      updatePricing(
                        modelId,
                        "batch_output_per_token",
                        e.target.value,
                      )
                    }
                    inputProps={{ step: 0.000001 }}
                    sx={{ width: 180 }}
                  />
                </Stack>

                <Divider />

                {/* Capabilities */}
                <Typography variant="body2" fontWeight={500}>
                  Capabilities
                </Typography>
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  {CAPABILITY_FIELDS.map(({ key, label }) => (
                    <FormControlLabel
                      key={key}
                      control={
                        <Switch
                          size="small"
                          checked={capabilities[key] ?? false}
                          onChange={(e) =>
                            updateCapability(modelId, key, e.target.checked)
                          }
                        />
                      }
                      label={<Typography variant="caption">{label}</Typography>}
                      sx={{ minWidth: 160 }}
                    />
                  ))}
                </Stack>
              </Stack>
            </AccordionDetails>
          </Accordion>
        );
      })}
    </Box>
  );
};

export default ModelDatabaseConfigTab;
