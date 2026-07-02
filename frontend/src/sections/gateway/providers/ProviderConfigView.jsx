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
import { useRemoveProvider } from "./hooks/useGatewayConfig";
import ConfirmDialog from "../components/ConfirmDialog";
import AddProviderDialog from "./AddProviderDialog";

const ProviderConfigView = ({ config, orgConfig, gatewayId }) => {
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [editTarget, setEditTarget] = useState(null);
  const removeProvider = useRemoveProvider();
  // Try gateway config first, fall back to org config providers
  const providers = config?.providers || orgConfig?.providers;

  if (!providers || Object.keys(providers).length === 0) {
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

  const entries =
    typeof providers === "object" && !Array.isArray(providers)
      ? Object.entries(providers)
      : [];

  return (
    <Stack spacing={2}>
      {entries.map(([name, providerConfig]) => (
        <Card key={name}>
          <CardContent>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              mb={1.5}
            >
              <Typography variant="h6">{name}</Typography>
              <Stack direction="row" spacing={0.5}>
                <IconButton
                  size="small"
                  onClick={() =>
                    setEditTarget({ name, config: providerConfig })
                  }
                  title="Edit provider"
                >
                  <Iconify icon="mdi:pencil-outline" width={18} />
                </IconButton>
                <IconButton
                  size="small"
                  color="error"
                  onClick={() => setDeleteTarget(name)}
                  title="Remove provider"
                >
                  <Iconify icon="mdi:delete-outline" width={18} />
                </IconButton>
              </Stack>
            </Stack>

            <Stack spacing={1}>
              <InfoLine
                label="Base URL"
                value={
                  providerConfig.base_url ?? providerConfig.baseUrl ?? "\u2014"
                }
              />
              <InfoLine
                label="API Format"
                value={
                  providerConfig.api_format ??
                  providerConfig.apiFormat ??
                  "\u2014"
                }
              />
              <InfoLine
                label="API Key"
                value={
                  "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022 (configured)"
                }
                mono={false}
              />
              <InfoLine
                label="Timeout"
                value={providerConfig.timeout ?? "\u2014"}
              />
              <InfoLine
                label="Max Concurrent"
                value={
                  providerConfig.max_concurrent ??
                  providerConfig.maxConcurrent ??
                  "\u2014"
                }
              />
              <InfoLine
                label="Connection Pool"
                value={
                  providerConfig.conn_pool_size ??
                  providerConfig.connPoolSize ??
                  "\u2014"
                }
              />

              <Divider sx={{ my: 1 }} />

              <Box>
                <Typography variant="subtitle2" mb={0.5}>
                  Models
                </Typography>
                {Array.isArray(providerConfig.models) &&
                providerConfig.models.length > 0 ? (
                  <Stack direction="row" flexWrap="wrap" gap={0.5}>
                    {providerConfig.models.map((m) => (
                      <Chip key={m} label={m} size="small" variant="outlined" />
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
      ))}
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        title="Remove Provider"
        message={`Remove provider "${deleteTarget}"? This will remove it from the gateway config and trigger a reload.`}
        typeToConfirm={deleteTarget || ""}
        confirmLabel="Remove"
        confirmColor="error"
        isLoading={removeProvider.isPending}
        onConfirm={() =>
          removeProvider.mutate(
            { gatewayId, name: deleteTarget },
            {
              onSuccess: () => {
                enqueueSnackbar(`Provider "${deleteTarget}" removed`, {
                  variant: "success",
                });
                setDeleteTarget(null);
              },
              onError: () => {
                enqueueSnackbar("Failed to remove provider", {
                  variant: "error",
                });
              },
            },
          )
        }
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
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  mono: PropTypes.bool,
};

ProviderConfigView.propTypes = {
  config: PropTypes.object,
  orgConfig: PropTypes.object,
  gatewayId: PropTypes.string,
};

export default ProviderConfigView;
