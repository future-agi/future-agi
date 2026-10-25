import React, { useState, useMemo } from "react";
import CodeBlock from "src/components/code-block";
import { QUICKSTART_GATEWAY } from "src/components/empty-state/quickstarts";
import {
  Box,
  Typography,
  Button,
  Stack,
  Chip,
  Skeleton,
  IconButton,
  Grid,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useGatewayContext } from "./context/useGatewayContext";
import { useProviderHealth } from "./providers/hooks/useGatewayConfig";
import { useAnalyticsOverview } from "./analytics/hooks/useAnalyticsOverview";

import { val } from "./utils/analyticsHelpers";
import { formatCost } from "./utils/formatters";
import { GATEWAY_ICONS } from "./constants/gatewayIcons";
import PageHeader from "src/components/page-header";
import MetricStrip from "src/components/metric-strip";
import Section from "src/components/section";
import PageErrorState from "./components/PageErrorState";
import GettingStartedCard from "./components/GettingStartedCard";
import { useApiKeys } from "./keys/hooks/useApiKeys";
import SvgColor from "src/components/svg-color";
import StatusBadge from "src/components/status-badge/StatusBadge";

const QUICK_LINKS = [
  {
    label: "API keys",
    icon: GATEWAY_ICONS.keys,
    path: "/dashboard/gateway/keys",
  },
  {
    label: "Providers",
    icon: GATEWAY_ICONS.providers,
    path: "/dashboard/gateway/providers",
  },
  {
    label: "Analytics",
    icon: GATEWAY_ICONS.analytics,
    path: "/dashboard/gateway/analytics",
  },
  {
    label: "Logs",
    icon: GATEWAY_ICONS.logs,
    path: "/dashboard/gateway/logs",
  },
  {
    label: "Guardrails",
    icon: GATEWAY_ICONS.guardrails,
    path: "/dashboard/gateway/guardrails",
  },
  {
    label: "Budgets",
    icon: GATEWAY_ICONS.budgets,
    path: "/dashboard/gateway/budgets",
  },
  {
    label: "Monitoring",
    icon: GATEWAY_ICONS.monitoring,
    path: "/dashboard/gateway/monitoring",
  },
  {
    label: "Settings",
    icon: GATEWAY_ICONS.settings,
    path: "/dashboard/gateway/settings",
  },
];

const GatewayOverviewSection = () => {
  const navigate = useNavigate();
  const { gateway, gatewayId, isLoading, error, refreshGateways } =
    useGatewayContext();
  const [copied, setCopied] = useState(false);

  const { data: providerHealth } = useProviderHealth(gatewayId);
  const now = useMemo(() => new Date().toISOString(), []);
  const dayAgo = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString();
  }, []);
  const { data: overview } = useAnalyticsOverview({
    start: dayAgo,
    end: now,
    gatewayId,
  });
  const { data: apiKeys } = useApiKeys(gatewayId);

  const completionState = useMemo(
    () => ({
      gatewayConnected: Boolean(gateway),
      hasProviders: (gateway?.providerCount ?? 0) > 0,
      hasKeys: Array.isArray(apiKeys) && apiKeys.length > 0,
      hasRequests: Number(val(overview?.total_requests) ?? 0) > 0,
    }),
    [gateway, apiKeys, overview],
  );

  const healthCheckMutation = useMutation({
    mutationFn: async (id) => {
      const res = await axiosInstance.post(
        endpoints.gateway.healthCheck(id),
        {},
      );
      return res.data;
    },
    onSuccess: () => {
      refreshGateways();
      enqueueSnackbar("Health check complete", { variant: "success" });
    },
    onError: () => {
      enqueueSnackbar("Health check failed", { variant: "error" });
    },
  });

  // Provider summary
  const providers = providerHealth?.providers;
  let providerList = [];
  if (Array.isArray(providers)) {
    providerList = providers.map((p) => ({
      ...p,
      name: p.name || p.provider_name || p.id,
    }));
  } else if (providers && typeof providers === "object") {
    providerList = Object.entries(providers).map(([name, info]) => ({
      name,
      ...(typeof info === "object" ? info : {}),
    }));
  }

  const handleCopyUrl = () => {
    if (gateway?.baseUrl) {
      navigator.clipboard.writeText(gateway.baseUrl);
      setCopied(true);
      enqueueSnackbar("Gateway URL copied", { variant: "success" });
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (isLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <Skeleton variant="circular" width={28} height={28} />
            <Skeleton width={200} height={40} />
          </Stack>
          <Skeleton width={100} height={36} variant="rounded" />
        </Stack>
        <Skeleton variant="rounded" height={80} sx={{ mb: 2 }} />
        <Grid container spacing={2} mb={3}>
          {[...Array(4)].map((_, i) => (
            <Grid item xs={6} sm={3} key={i}>
              <Skeleton variant="rounded" height={90} />
            </Grid>
          ))}
        </Grid>
        <Grid container spacing={2}>
          <Grid item xs={12} md={6}>
            <Skeleton variant="rounded" height={180} />
          </Grid>
          <Grid item xs={12} md={6}>
            <Skeleton variant="rounded" height={180} />
          </Grid>
        </Grid>
      </Box>
    );
  }

  if (error) {
    return (
      <Box p={3}>
        <PageErrorState
          message={`Failed to load gateway: ${error.message}`}
          onRetry={refreshGateways}
        />
      </Box>
    );
  }

  const latencyMs = val(overview?.avg_latency_ms);
  const requestCount = Number(val(overview?.total_requests) ?? 0);
  let latencyHint;
  if (latencyMs != null && requestCount > 0) {
    const ms = Number(latencyMs);
    latencyHint = ms < 200 ? "Good" : ms < 500 ? "Fair" : "Slow";
  }
  const errorRate = Number(val(overview?.error_rate) ?? 0);

  return (
    <Box p={3}>
      <PageHeader
        title={gateway.name}
        badge={<StatusBadge status={gateway.status} />}
        actions={
          <Button
            variant="outlined"
            size="small"
            startIcon={
              <SvgColor width={16} src="/assets/icons/agent/docs.svg" />
            }
            onClick={() => {
              window.open(
                "https://docs.futureagi.com/docs/command-center",
                "_blank",
              );
            }}
          >
            View docs
          </Button>
        }
      />

      <Section title="Gateway endpoint" variant="panel" sx={{ mb: 3 }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Box sx={{ minWidth: 0 }}>
            <Typography
              sx={{
                fontFamily: "monospace",
                fontSize: 13,
                color: "text.primary",
                wordBreak: "break-all",
              }}
            >
              {gateway.baseUrl}
            </Typography>
            <Typography sx={{ fontSize: 13, color: "text.tertiary", mt: 0.5 }}>
              Send OpenAI-compatible requests to this URL. Use your API key for
              authentication.
            </Typography>
          </Box>
          <Tooltip title={copied ? "Copied!" : "Copy URL"}>
            <IconButton onClick={handleCopyUrl} size="small">
              <Iconify
                icon={copied ? "solar:check-linear" : "solar:copy-linear"}
                width={16}
                sx={{ color: "text.secondary" }}
              />
            </IconButton>
          </Tooltip>
        </Stack>
      </Section>

      {/* Until the first request lands: the call to make, against this
          gateway's own URL. */}
      {requestCount === 0 && (
        <Section
          title="Send your first request"
          description="Any OpenAI-compatible client works. The overview fills in as requests arrive."
          sx={{ mb: 3 }}
        >
          <CodeBlock
            code={QUICKSTART_GATEWAY.snippets[0].code.replace(
              "https://gateway.futureagi.com/v1",
              gateway.baseUrl || "https://gateway.futureagi.com/v1",
            )}
          />
          <Typography
            sx={{
              fontSize: 12,
              lineHeight: "16px",
              color: "text.secondary",
              mt: 1,
            }}
          >
            {QUICKSTART_GATEWAY.waiting}
          </Typography>
        </Section>
      )}

      <GettingStartedCard completionState={completionState} />

      <MetricStrip
        sx={{ mb: 3 }}
        items={[
          {
            label: "Requests (24h)",
            value: requestCount.toLocaleString(),
            onClick: () => navigate("/dashboard/gateway/analytics"),
          },
          {
            label: "Cost (24h)",
            value: formatCost(val(overview?.total_cost)),
            onClick: () => navigate("/dashboard/gateway/analytics/cost"),
          },
          {
            label: "Avg latency",
            value:
              latencyMs != null
                ? `${Number(latencyMs).toFixed(0)}ms`
                : "\u2014",
            hint: latencyHint,
            onClick: () => navigate("/dashboard/gateway/analytics/latency"),
          },
          {
            label: "Error rate",
            value:
              val(overview?.error_rate) != null
                ? `${Number(val(overview.error_rate)).toFixed(1)}%`
                : "0%",
            hint: errorRate > 5 ? "Above 5%" : undefined,
            onClick: () => navigate("/dashboard/gateway/analytics/errors"),
          },
        ]}
      />

      <Section
        title="Provider status"
        sx={{ mb: 3 }}
        actions={
          <>
            <Chip
              label={`${gateway.providerCount ?? providerList.length} providers`}
              size="small"
              variant="outlined"
            />
            <Tooltip title="Check connectivity to all configured providers">
              <Button
                size="small"
                variant="outlined"
                onClick={() => healthCheckMutation.mutate(gatewayId)}
                disabled={healthCheckMutation.isPending}
                startIcon={
                  <Iconify
                    icon={
                      healthCheckMutation.isPending
                        ? "mdi:loading"
                        : "solar:heart-pulse-linear"
                    }
                    width={16}
                    sx={
                      healthCheckMutation.isPending
                        ? {
                            animation: "spin 1s linear infinite",
                            "@keyframes spin": {
                              from: { transform: "rotate(0deg)" },
                              to: { transform: "rotate(360deg)" },
                            },
                          }
                        : undefined
                    }
                  />
                }
              >
                {healthCheckMutation.isPending ? "Checking..." : "Health check"}
              </Button>
            </Tooltip>
          </>
        }
      >
        {providerList.length > 0 ? (
          <Stack direction="row" flexWrap="wrap" gap={1}>
            {providerList.map((p, idx) => {
              const isHealthy = p.healthy !== false && p.status !== "unhealthy";
              return (
                <Chip
                  key={p.name || idx}
                  label={p.name || `Provider ${idx + 1}`}
                  variant="outlined"
                  size="small"
                  sx={
                    isHealthy
                      ? undefined
                      : {
                          color: "status.danger.fg",
                          borderColor: "status.danger.border",
                          bgcolor: "status.danger.bg",
                        }
                  }
                />
              );
            })}
          </Stack>
        ) : (
          <Typography sx={{ fontSize: 13, color: "text.tertiary" }}>
            No provider data. Run a health check to see provider status.
          </Typography>
        )}
        <Stack direction="row" spacing={4} mt={2}>
          <Box>
            <Typography sx={{ fontSize: 12, color: "text.tertiary" }}>
              Models
            </Typography>
            <Typography sx={{ fontSize: 13, color: "text.primary" }}>
              {gateway.modelCount ?? 0}
            </Typography>
          </Box>
          <Box>
            <Typography sx={{ fontSize: 12, color: "text.tertiary" }}>
              Last check
            </Typography>
            <Typography sx={{ fontSize: 13, color: "text.primary" }}>
              {gateway.lastHealthCheck
                ? new Date(gateway.lastHealthCheck).toLocaleString()
                : "Never"}
            </Typography>
          </Box>
        </Stack>
      </Section>

      <Section title="Quick links">
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(2, minmax(0, 1fr))",
              sm: "repeat(4, minmax(0, 1fr))",
            },
            gap: 1,
          }}
        >
          {QUICK_LINKS.map((link) => (
            <Box
              key={link.path}
              component="button"
              type="button"
              onClick={() => navigate(link.path)}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                px: 1.5,
                py: 1,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
                bgcolor: "transparent",
                color: "text.primary",
                font: "inherit",
                fontSize: 13,
                textAlign: "left",
                cursor: "pointer",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Iconify
                icon={link.icon}
                width={16}
                sx={{ color: "text.secondary", flexShrink: 0 }}
              />
              {link.label}
            </Box>
          ))}
        </Box>
      </Section>
    </Box>
  );
};

export default GatewayOverviewSection;
