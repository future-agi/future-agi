/* eslint-disable react/prop-types */
import React from "react";
import { Stack, Typography, Button, Chip, Alert, Box } from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
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
          startIcon={<Iconify icon="solar:arrow-right-up-bold" width={20} />}
          component={RouterLink}
          href={paths.dashboard.gateway.providers}
          target="_blank"
        >
          Manage Providers
        </Button>
      </Stack>

      <Alert severity="info" variant="outlined">
        Providers are managed in the <strong>Providers</strong> tab. Changes
        here are per-org overrides (e.g., custom API keys, model restrictions).
      </Alert>

      {configuredProviders.length === 0 ? (
        <Section variant="panel">
          <Box sx={{ textAlign: "center" }}>
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
              startIcon={<Iconify icon="solar:add-circle-bold" width={20} />}
            >
              Add Provider
            </Button>
          </Box>
        </Section>
      ) : (
        configuredProviders.map((p) => {
          const name = p.name || p.provider_id || p.providerId || "unknown";
          const models = Array.isArray(p.models) ? p.models : [];
          const status = (p.status || "unknown").toLowerCase();

          return (
            <Section variant="panel" key={name}>
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
                  <StatusBadge
                    status={status}
                    role={
                      status === "healthy" || status === "degraded"
                        ? undefined
                        : "danger"
                    }
                    size="sm"
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
                    <Chip key={m} label={m} size="small" variant="outlined" />
                  ))}
                </Stack>
              )}
            </Section>
          );
        })
      )}
    </Stack>
  );
};

export default ProviderConfigTab;
