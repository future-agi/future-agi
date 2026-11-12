/* eslint-disable react/prop-types */
import Section from "src/components/section";
import React from "react";
import {
  Stack,
  Card,
  Typography,
  Button,
  Chip,
  Alert,
  Link,
} from "@mui/material";
import EmptyState from "src/components/empty-state";
import StatusBadge from "src/components/status-badge/StatusBadge";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import { useProviderHealth } from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";
import {
  PROVIDER_STATUS,
  providerStatusKey,
} from "../providers/provider-rows";

// Preserve the provider truth model while using the dev DS badge.
const STATUS_ROLE = {
  healthy: "success",
  warning: "warning",
  error: "danger",
  neutral: "neutral",
};

const ProviderConfigTab = ({
  providers: _providers = undefined,
  onChange: _onChange = undefined,
}) => {
  const { gatewayId } = useGatewayContext();
  const {
    data: healthData,
    isPending,
    isLoading,
    isFetching,
    isError,
    error,
    refetch,
  } = useProviderHealth(gatewayId);
  const readFailed = isError || Boolean(error);
  const hasData = healthData != null;
  const loading = isPending || isLoading || (!hasData && !readFailed);

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
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
      >
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

      <Alert severity="info" variant="outlined" sx={{ py: 0.5 }}>
        This list is read-only. To change a provider&apos;s key, base URL or
        models, open{" "}
        <Link component={RouterLink} href={paths.dashboard.gateway.providers}>
          Providers
        </Link>{" "}
        and use the provider&apos;s Edit button.
      </Alert>

      {readFailed && (
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              onClick={() => refetch()}
              disabled={isFetching || !gatewayId}
            >
              Retry
            </Button>
          }
        >
          Could not load providers.
          {hasData &&
            " Showing the last loaded providers; this list may be stale."}
        </Alert>
      )}
      {(loading || isFetching) && (
        <Typography role="status" variant="body2" color="text.secondary">
          {hasData ? "Refreshing providers…" : "Loading providers…"}
        </Typography>
      )}

      {configuredProviders.length === 0 &&
      hasData &&
      !readFailed &&
      !loading ? (
        <Section variant="panel" component={Card} elevation={0} sx={{ p: 0 }}>
          <EmptyState
            icon="solar:server-linear"
            title="No providers yet"
            description="Add a provider on the Providers page."
            action={
              <Button
                variant="contained"
                color="primary"
                size="small"
                component={RouterLink}
                href={paths.dashboard.gateway.providers}
              >
                Add provider
              </Button>
            }
            sx={{ pt: 6, pb: 6 }}
          />
        </Section>
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
          const statusRole = STATUS_ROLE[statusInfo.tone] || "neutral";

          return (
            <Section
              component={Card}
              elevation={0}
              key={name}
              variant="panel"
            >
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
                    status={providerStatusKey(p)}
                    label={status}
                    role={statusRole}
                    size="sm"
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
            </Section>
          );
        })
      )}
    </Stack>
  );
};

export default ProviderConfigTab;
