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
  Link,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useProviderHealth } from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";
import { PROVIDER_STATUS, providerStatusKey } from "../providers/provider-rows";

// A status tone as an MUI Chip colour.
const CHIP_COLOR = {
  healthy: "success",
  warning: "warning",
  error: "error",
  neutral: "default",
};

const ProviderConfigTab = ({
  providers: _providers = undefined,
  onChange: _onChange = undefined,
}) => {
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
        This list is read-only. To change a provider&apos;s key, base URL or
        models, open{" "}
        <Link component={RouterLink} href={paths.dashboard.gateway.providers}>
          Providers
        </Link>{" "}
        and use the provider&apos;s Edit button.
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
          // The org's own health, read as the Providers page reads it
          // (provider-rows.js): a provider the config push leaves out reads
          // Not pushed, and one with fewer than 5 requests in 24 h reads Too
          // few requests, never Healthy.
          const statusInfo =
            PROVIDER_STATUS[providerStatusKey(p)] || PROVIDER_STATUS.unknown;
          const status = statusInfo.label;
          const statusColor = CHIP_COLOR[statusInfo.tone] || "default";

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
                {p.status_reason && (
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    component="div"
                    sx={{ mt: 0.5, pl: 3.5 }}
                  >
                    {p.status_reason}
                  </Typography>
                )}
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
