/* eslint-disable react/prop-types */
import React from "react";
import {
  Stack,
  Card,
  CardContent,
  Typography,
  Button,
  Chip,
  Alert,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useProviderHealth } from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";

const ProviderConfigTab = ({ providers: _providers, onChange: _onChange }) => {
  const { gatewayId } = useGatewayContext();
  const { data: healthData } = useProviderHealth(gatewayId);

  // Get actual configured providers from the health endpoint
  const configuredProviders = React.useMemo(() => {
    const raw = healthData?.providers || [];
    const list = Array.isArray(raw) ? raw : Object.values(raw);
    return list.filter(
      (p) => p && (Array.isArray(p.models) ? p.models.length > 0 : true),
    );
  }, [healthData]);

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="subtitle1">Providers</Typography>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:arrow-right-up-bold" width={18} />}
          component={RouterLink}
          href={paths.dashboard.gateway.providers}
          target="_blank"
        >
          Manage Providers
        </Button>
      </Stack>

      <Alert severity="info" variant="outlined" sx={{ py: 0.5 }}>
        Providers are managed in the <strong>Providers</strong> tab. Changes
        here are per-org overrides (e.g., custom API keys, model restrictions).
      </Alert>

      {configuredProviders.length === 0 ? (
        <Card variant="outlined">
          <CardContent sx={{ textAlign: "center", py: 4 }}>
            <Iconify
              icon="solar:server-bold-duotone"
              width={40}
              sx={{ color: "text.disabled", mb: 1 }}
            />
            <Typography variant="body2" color="text.secondary">
              No providers configured yet.
            </Typography>
            <Button
              size="small"
              sx={{ mt: 1 }}
              component={RouterLink}
              href={paths.dashboard.gateway.providers}
              startIcon={<Iconify icon="solar:add-circle-bold" width={18} />}
            >
              Add Provider
            </Button>
          </CardContent>
        </Card>
      ) : (
        configuredProviders.map((p) => {
          const name = p.name || p.provider_id || p.providerId || "unknown";
          const models = Array.isArray(p.models) ? p.models : [];
          const status = (p.status || "unknown").toLowerCase();
          const statusColor =
            status === "healthy"
              ? "success"
              : status === "degraded"
                ? "warning"
                : "error";

          return (
            <Card key={name} variant="outlined">
              <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                >
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Iconify
                      icon="solar:server-bold-duotone"
                      width={20}
                      sx={{ color: "primary.main" }}
                    />
                    <Typography variant="subtitle2">{name}</Typography>
                    <Chip
                      label={status}
                      size="small"
                      color={statusColor}
                      variant="outlined"
                      sx={{ height: 20, fontSize: "0.7rem" }}
                    />
                  </Stack>
                </Stack>
                {models.length > 0 && (
                  <Stack
                    direction="row"
                    spacing={0.5}
                    sx={{ mt: 1, pl: 3.5, flexWrap: "wrap", gap: 0.5 }}
                  >
                    {models.map((m) => (
                      <Chip
                        key={m}
                        label={m}
                        size="small"
                        variant="outlined"
                        sx={{ height: 22, fontSize: "0.7rem" }}
                      />
                    ))}
                  </Stack>
                )}
              </CardContent>
            </Card>
          );
        })
      )}
    </Stack>
  );
};

export default ProviderConfigTab;
