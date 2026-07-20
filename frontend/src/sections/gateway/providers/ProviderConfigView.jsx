import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Card,
  CardContent,
  Typography,
  Box,
  Stack,
  Chip,
  Divider,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import { useDeleteProviderCredential } from "./hooks/useProviderCredentials";
import { useRemoveProvider } from "./hooks/useGatewayConfig";
import ConfirmDialog from "../components/ConfirmDialog";
import AddProviderDialog from "./AddProviderDialog";
import { formatProviderName } from "./utils/formatProviderName";

const ProviderConfigView = ({
  config,
  orgConfig,
  gatewayId,
  providerCredentials,
}) => {
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [editTarget, setEditTarget] = useState(null);
  const deleteCredential = useDeleteProviderCredential();
  const removeProvider = useRemoveProvider();

  // Build a merged view: provider-credentials (preferred) + gateway config (fallback)
  // Provider-credentials have display_name, encrypted keys, and auto-sync.
  // Gateway config is the legacy source for providers not yet migrated.
  const credentialMap = {};
  if (Array.isArray(providerCredentials)) {
    providerCredentials.forEach((cred) => {
      if (cred.provider_name) {
        credentialMap[cred.provider_name] = cred;
      }
    });
  }

  const gatewayProviders = config?.providers || orgConfig?.providers || {};
  const hasGatewayProviders =
    gatewayProviders && Object.keys(gatewayProviders).length > 0;
  const hasCredentials =
    providerCredentials && providerCredentials.length > 0;

  // Merge: credentials take priority, then gateway config for legacy providers
  const mergedEntries = [];

  // Add provider-credentials entries first
  if (hasCredentials) {
    providerCredentials.forEach((cred) => {
      mergedEntries.push({
        key: cred.provider_name,
        source: "credential",
        data: cred,
      });
    });
  }

  // Add gateway config entries not already covered by credentials
  if (hasGatewayProviders) {
    Object.entries(gatewayProviders).forEach(([name, cfg]) => {
      if (!credentialMap[name]) {
        mergedEntries.push({
          key: name,
          source: "gateway",
          data: { ...cfg, provider_name: name },
        });
      }
    });
  }

  if (mergedEntries.length === 0) {
    return (
      <Card sx={{ p: 4 }}>
        <Box
          display="flex"
          justifyContent="center"
          alignItems="center"
          minHeight={200}
        >
          <Typography color="text.secondary">
            No provider configuration available.
          </Typography>
        </Box>
      </Card>
    );
  }

  const handleDelete = () => {
    const entry = deleteTarget;
    if (!entry) return;

    if (entry.source === "credential" && entry.data?.id) {
      deleteCredential.mutate(entry.data.id, {
        onSuccess: () => {
          enqueueSnackbar(`Provider "${entry.key}" removed`, {
            variant: "success",
          });
          setDeleteTarget(null);
        },
        onError: () => {
          enqueueSnackbar("Failed to remove provider", { variant: "error" });
        },
      });
    } else {
      // Legacy gateway config removal
      removeProvider.mutate(
        { gatewayId, name: entry.key },
        {
          onSuccess: () => {
            enqueueSnackbar(`Provider "${entry.key}" removed`, {
              variant: "success",
            });
            setDeleteTarget(null);
          },
          onError: () => {
            enqueueSnackbar("Failed to remove provider", { variant: "error" });
          },
        },
      );
    }
  };

  return (
    <Stack spacing={2}>
      {mergedEntries.map((entry) => {
        const { key, source, data } = entry;
        const isCredential = source === "credential";

        // Extract display info from either source
        const heading = formatProviderName(
          {
            name: key,
            display_name: data.display_name,
            provider_name: data.provider_name,
          },
          undefined,
        );
        const baseUrl =
          data.base_url ?? data.baseUrl ?? data.config?.base_url ?? "—";
        const apiFormat =
          data.api_format ??
          data.apiFormat ??
          data.config?.api_format ??
          "—";
        const models = isCredential
          ? data.models_list ?? []
          : data.models ?? data.config?.models ?? [];
        const timeout = isCredential
          ? data.default_timeout_seconds
          : data.default_timeout ?? data.defaultTimeout;
        const maxConcurrent = isCredential
          ? data.max_concurrent
          : data.max_concurrent ?? data.maxConcurrent;
        const connPool = isCredential
          ? data.conn_pool_size
          : data.conn_pool_size ?? data.connPoolSize;
        const isActive = isCredential ? data.is_active : undefined;
        const maskedKey = isCredential
          ? data.credentials?.api_key || "•••••••• (configured)"
          : "•••••••• (configured)";

        return (
          <Card key={key}>
            <CardContent>
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
                mb={1.5}
              >
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="h6">{heading}</Typography>
                  {isActive === false && (
                    <Chip label="Inactive" size="small" color="default" />
                  )}
                </Stack>
                <Stack direction="row" spacing={0.5}>
                  <IconButton
                    size="small"
                    onClick={() =>
                      setEditTarget({
                        name: key,
                        provider_name: data.provider_name || key,
                        id: data.id,
                        config: data.config || data,
                        display_name: data.display_name,
                        base_url: baseUrl !== "—" ? baseUrl : "",
                        api_format: apiFormat !== "—" ? apiFormat : "",
                        models_list: models,
                      })
                    }
                    title="Edit provider"
                  >
                    <Iconify icon="mdi:pencil-outline" width={18} />
                  </IconButton>
                  <IconButton
                    size="small"
                    color="error"
                    onClick={() => setDeleteTarget(entry)}
                    title="Remove provider"
                  >
                    <Iconify icon="mdi:delete-outline" width={18} />
                  </IconButton>
                </Stack>
              </Stack>

              <Stack spacing={1}>
                <InfoLine label="Base URL" value={baseUrl} />
                <InfoLine label="API Format" value={apiFormat} />
                <InfoLine label="API Key" value={maskedKey} mono={false} />
                <InfoLine label="Timeout" value={timeout ?? "—"} />
                <InfoLine label="Max Concurrent" value={maxConcurrent ?? "—"} />
                <InfoLine label="Connection Pool" value={connPool ?? "—"} />
                {isCredential && data.last_rotated_at && (
                  <InfoLine
                    label="Last Rotated"
                    value={new Date(data.last_rotated_at).toLocaleString()}
                  />
                )}

                <Divider sx={{ my: 1 }} />

                <Box>
                  <Typography variant="subtitle2" mb={0.5}>
                    Models
                  </Typography>
                  {Array.isArray(models) && models.length > 0 ? (
                    <Stack direction="row" flexWrap="wrap" gap={0.5}>
                      {models.map((m) => (
                        <Chip
                          key={m}
                          label={m}
                          size="small"
                          variant="outlined"
                        />
                      ))}
                    </Stack>
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      No models configured
                    </Typography>
                  )}
                </Box>
              </Stack>
            </CardContent>
          </Card>
        );
      })}
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        title="Remove Provider"
        message={`Remove provider "${deleteTarget?.key}"? This will remove it from the gateway and trigger a sync.`}
        typeToConfirm={deleteTarget?.key || ""}
        confirmLabel="Remove"
        confirmColor="error"
        isLoading={deleteCredential.isPending || removeProvider.isPending}
        onConfirm={handleDelete}
      />
      <AddProviderDialog
        open={Boolean(editTarget)}
        onClose={() => setEditTarget(null)}
        gatewayId={gatewayId}
        provider={editTarget}
      />
    </Stack>
  );
};

const InfoLine = ({ label, value, mono = false }) => (
  <Stack direction="row" spacing={2}>
    <Typography variant="body2" color="text.secondary" sx={{ minWidth: 140 }}>
      {label}
    </Typography>
    <Typography
      variant="body2"
      sx={mono ? { fontFamily: "monospace" } : undefined}
    >
      {String(value)}
    </Typography>
  </Stack>
);

InfoLine.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired,
  mono: PropTypes.bool,
};

ProviderConfigView.propTypes = {
  config: PropTypes.shape({
    providers: PropTypes.object,
  }),
  orgConfig: PropTypes.shape({
    providers: PropTypes.object,
  }),
  gatewayId: PropTypes.string,
  providerCredentials: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      provider_name: PropTypes.string,
      display_name: PropTypes.string,
      base_url: PropTypes.string,
      api_format: PropTypes.string,
      models_list: PropTypes.array,
      is_active: PropTypes.bool,
      last_rotated_at: PropTypes.string,
    }),
  ),
};

export default ProviderConfigView;
