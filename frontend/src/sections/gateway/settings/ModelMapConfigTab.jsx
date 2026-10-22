/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  TextField,
  Paper,
  Alert,
  Chip,
  Button,
  IconButton,
  Autocomplete,
} from "@mui/material";
import { Icon } from "@iconify/react";
import { useAvailableModels } from "../hooks/useAvailableModels";

const ModelMapConfigTab = ({ modelMap, onChange }) => {
  const availableModels = useAvailableModels();
  const map = modelMap || {};
  const [newModelName, setNewModelName] = useState("");
  const [newProviderId, setNewProviderId] = useState("");

  const entries = Object.entries(map);

  const addMapping = () => {
    const model = newModelName.trim();
    const provider = newProviderId.trim();
    if (!model || !provider) return;
    if (map[model]) return; // duplicate
    onChange({ ...map, [model]: provider });
    setNewModelName("");
    setNewProviderId("");
  };

  const removeMapping = (modelName) => {
    const updated = { ...map };
    delete updated[modelName];
    onChange(updated);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="mdi:swap-horizontal" width={24} />
        <Typography variant="h6">Model Map</Typography>
      </Stack>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Alert severity="info" sx={{ mb: 2 }}>
          <Typography variant="body2" sx={{ mb: 0.5 }}>
            Map a model name to a provider. For your organization&apos;s API
            keys, a mapped name goes only to the provider you map it to, and
            only when that is one of your organization&apos;s own providers
            (enabled, with a key); a name mapped to any other provider is not
            used.
          </Typography>
          <Typography variant="body2" sx={{ mb: 0.5 }}>
            If that provider lists exactly one model, the request is sent as
            that model, so the name can be an alias (for example fast mapped to
            a provider that lists gpt-4o-mini). Otherwise the request is sent
            under the name it used.
          </Typography>
          <Typography variant="body2">
            A model with no mapping goes to the first of your providers that
            lists it, sorted by provider ID, unless a routing strategy spreads
            it across them.
          </Typography>
        </Alert>

        {entries.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            No model mappings configured. Add mappings to route specific models
            to specific providers.
          </Typography>
        )}

        {entries.length > 0 && (
          <Stack spacing={1} sx={{ mb: 2 }}>
            {entries.map(([modelName, providerId]) => (
              <Stack
                key={modelName}
                direction="row"
                spacing={1}
                alignItems="center"
              >
                <Chip
                  label={modelName}
                  size="small"
                  color="primary"
                  variant="outlined"
                />
                <Icon icon="mdi:arrow-right" width={16} />
                <Chip
                  label={providerId}
                  size="small"
                  color="secondary"
                  variant="outlined"
                />
                <IconButton
                  size="small"
                  onClick={() => removeMapping(modelName)}
                >
                  <Icon icon="mdi:delete-outline" width={16} color="#d32f2f" />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        )}

        <Stack direction="row" spacing={1} alignItems="center">
          <Autocomplete
            freeSolo
            options={availableModels.filter((m) => !map[m])}
            inputValue={newModelName}
            onInputChange={(_, val) => setNewModelName(val)}
            onChange={(_, val) => {
              if (val) {
                setNewModelName(val);
              }
            }}
            renderInput={(params) => (
              <TextField
                {...params}
                size="small"
                placeholder="Model name (e.g. gpt-4o)"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addMapping();
                  }
                }}
              />
            )}
            sx={{ width: 220 }}
          />
          <Icon icon="mdi:arrow-right" width={16} />
          <TextField
            size="small"
            placeholder="Provider ID (e.g. openai)"
            value={newProviderId}
            onChange={(e) => setNewProviderId(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addMapping();
              }
            }}
            sx={{ width: 220 }}
          />
          <Button size="small" variant="outlined" onClick={addMapping}>
            Add
          </Button>
        </Stack>
      </Paper>
    </Box>
  );
};

export default ModelMapConfigTab;
