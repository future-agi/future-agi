/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  TextField,
  MenuItem,
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

// What each override does on the gateway (modeldb/orgdb, the cost and
// validation plugins, auto, /v1/models, /v1/capabilities, /v1/count_tokens).
// Change them together.
const LIMITS_HELP =
  "A request asking for more output than Max Output Tokens is refused (400 max_tokens_exceeds_limit); one that sets no max_tokens is sent with that limit. An estimated input above Max Input Tokens is refused (413 input_too_large). Limits apply to managed models too.";
const PRICING_HELP =
  "Prices calls made with your own provider keys. A Cost Tracking custom price for the same model wins over these; a blank field keeps the model database's price. Managed models are always billed at the managed catalog price.";
const CAPABILITIES_HELP =
  "Off refuses a request that uses the capability (400 model_capability_error). On allows it even where the model database says the model lacks it. Default keeps the model database's value. Prompt Caching only changes what /v1/models and /v1/capabilities report.";

// A capability override is three-state: unset (the model database's value),
// on or off.
const CAPABILITY_STATES = [
  { value: "default", label: "Default" },
  { value: "on", label: "On" },
  { value: "off", label: "Off" },
];

const capabilityState = (v) => {
  if (v === true) return "on";
  if (v === false) return "off";
  return "default";
};

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

  // state is "default" (remove the override), "on" or "off".
  const updateCapability = (modelId, field, state) => {
    const current = overrides[modelId] || {};
    const capabilities = { ...(current.capabilities || {}) };
    if (state === "default") delete capabilities[field];
    else capabilities[field] = state === "on";
    updateOverrides({
      ...overrides,
      [modelId]: { ...current, capabilities },
    });
  };

  const modelIds = Object.keys(overrides);

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="mdi:database-cog-outline" width={24} />
        <Typography variant="h6">Model Database Overrides</Typography>
      </Stack>

      <Alert severity="info" sx={{ mb: 2 }}>
        Override model metadata (token limits, pricing, capabilities) for this
        organization&apos;s requests. Overrides are merged on top of the
        gateway&apos;s model database: a field you leave blank keeps its value.
        Prices: a Cost Tracking custom price wins over these, and managed models
        stay on the managed catalog price.
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
            <AccordionSummary expandIcon={<Icon icon="mdi:chevron-down" />}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ flex: 1 }}
              >
                <Icon icon="mdi:cube-outline" width={18} />
                <Typography variant="subtitle1" fontWeight="bold">
                  {modelId}
                </Typography>
                <Box sx={{ flex: 1 }} />
                <IconButton
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeModel(modelId);
                  }}
                >
                  <Icon icon="mdi:delete-outline" width={18} color="#d32f2f" />
                </IconButton>
              </Stack>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={2}>
                {/* Token Limits */}
                <Typography variant="body2" fontWeight={500}>
                  Token Limits
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {LIMITS_HELP}
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
                <Typography variant="caption" color="text.secondary">
                  {PRICING_HELP}
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
                  {/* No batch price fields: the gateway prices no call at
                      batch rates (its batch jobs call the provider's regular
                      API), so a batch price would change nothing. Stored
                      values are left as they are. */}
                </Stack>

                <Divider />

                {/* Capabilities */}
                <Typography variant="body2" fontWeight={500}>
                  Capabilities
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {CAPABILITIES_HELP}
                </Typography>
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  {CAPABILITY_FIELDS.map(({ key, label }) => (
                    <TextField
                      key={key}
                      select
                      size="small"
                      label={label}
                      value={capabilityState(capabilities[key])}
                      onChange={(e) =>
                        updateCapability(modelId, key, e.target.value)
                      }
                      sx={{ width: 180 }}
                    >
                      {CAPABILITY_STATES.map((st) => (
                        <MenuItem key={st.value} value={st.value}>
                          {st.label}
                        </MenuItem>
                      ))}
                    </TextField>
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
