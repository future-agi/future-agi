import { useConfirmRemoval } from "../hooks/useConfirmRemoval";
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
  MenuItem,
} from "@mui/material";
import { Icon } from "@iconify/react";
import { useAvailableModels } from "../hooks/useAvailableModels";
import { useProviderHealth } from "../providers/hooks/useGatewayConfig";

const ModelMapConfigTab = ({ modelMap, onChange, gatewayId }) => {
  const [requestRemoval, confirmation] = useConfirmRemoval();
  const availableModels = useAvailableModels();
  const map = modelMap || {};
  const [newModelName, setNewModelName] = useState("");
  const [newProviderId, setNewProviderId] = useState("");
  const providerQuery = useProviderHealth(gatewayId);
  const providers = Array.isArray(providerQuery.data?.providers) ? providerQuery.data.providers : [];
  const providerReadFailed = providerQuery.isError || Boolean(providerQuery.error);
  const selectedProvider = providers.find((provider) => provider.id === newProviderId);
  const canAdd = Boolean(gatewayId && newModelName.trim() && selectedProvider &&
    selectedProvider.pushed !== false && !providerReadFailed && !providerQuery.isLoading &&
    !Object.prototype.hasOwnProperty.call(map, newModelName.trim()));

  const entries = Object.entries(map);

  const addMapping = () => {
    const model = newModelName.trim();
    const provider = newProviderId.trim();
    if (!canAdd) return;
    onChange({ ...map, [model]: provider });
    setNewModelName("");
    setNewProviderId("");
  };

  const removeMapping = (modelName) => {
    requestRemoval(`Model mapping ${modelName}`, () => {
      const updated = { ...map };
      delete updated[modelName];
      onChange(updated);
    });
  };

  return (
    <Box>
      {confirmation}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="solar:transfer-horizontal-linear" width={24} />
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
                  aria-label={`Remove model mapping ${modelName}`}
                  onClick={() => removeMapping(modelName)}
                >
                  <Icon icon="solar:trash-bin-trash-linear" width={16} />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        )}

        {!gatewayId ? (
          <Alert severity="info" sx={{ mb: 2 }}>Select a gateway to choose a configured provider.</Alert>
        ) : providerReadFailed ? (
          <Alert severity="warning" sx={{ mb: 2 }} action={
            <Button onClick={() => providerQuery.refetch()} disabled={providerQuery.isFetching}>Retry providers</Button>
          }>
            Could not load configured providers. Existing mappings are unchanged.
          </Alert>
        ) : providerQuery.isLoading ? (
          <Typography role="status" variant="body2" sx={{ mb: 2 }}>Loading configured providers…</Typography>
        ) : providerQuery.data && providers.length === 0 ? (
          <Alert severity="info" sx={{ mb: 2 }}>No configured providers. Add one on the Providers page before creating a mapping.</Alert>
        ) : null}
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "stretch", sm: "center" }}>
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
            select
            size="small"
            label="Configured provider"
            value={selectedProvider ? newProviderId : ""}
            disabled={!gatewayId || providerReadFailed || providerQuery.isLoading || providers.length === 0}
            onChange={(e) => setNewProviderId(e.target.value)}
            sx={{ minWidth: 220, flex: 1 }}
          >
            {providers.map((provider) => (
              <MenuItem key={provider.id} value={provider.id} disabled={provider.pushed === false}>
                {provider.display_name || provider.name || provider.id} · {provider.id} · {Array.isArray(provider.models) ? `${provider.models.length} configured model${provider.models.length === 1 ? "" : "s"}` : "Models not reported"}
                {provider.pushed === false ? " · Not sent to gateway" : ""}
              </MenuItem>
            ))}
          </TextField>
          <Button size="small" variant="outlined" disabled={!canAdd} onClick={addMapping}>
            Add
          </Button>
        </Stack>
        {selectedProvider && !providerReadFailed && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {selectedProvider.models?.length === 1
              ? `This alias will be sent as ${selectedProvider.models[0]}.`
              : "This provider does not list exactly one model; the request keeps the mapped model name. Confirm that the provider accepts it."}
            {" "}Configured models are not a live health check.
          </Typography>
        )}
      </Paper>
    </Box>
  );
};

export default ModelMapConfigTab;
