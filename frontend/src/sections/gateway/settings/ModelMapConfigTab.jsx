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
        <Icon icon="solar:transfer-horizontal-linear" width={24} />
        <Typography variant="h6">Model Map</Typography>
      </Stack>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Alert severity="info" sx={{ mb: 2 }}>
          Map model names to specific providers. When a request uses a mapped
          model name, it routes to the specified provider instead of the default
          registry lookup.
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
                <Icon icon="solar:arrow-right-linear" width={16} />
                <Chip
                  label={providerId}
                  size="small"
                  color="secondary"
                  variant="outlined"
                />
                <IconButton
                  sx={{ color: "status.danger.fg" }}
                  size="small"
                  onClick={() => removeMapping(modelName)}
                >
                  <Icon icon="solar:trash-bin-trash-linear" width={16} />
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
          <Icon icon="solar:arrow-right-linear" width={16} />
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
