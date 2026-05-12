import React, { useState, useMemo } from "react";
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  Stack,
  Chip,
  Skeleton,
  IconButton,
  Grid,
  CardActionArea,
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
import SectionHeader from "./components/SectionHeader";
import PageErrorState from "./components/PageErrorState";
import GettingStartedCard from "./components/GettingStartedCard";
import { useApiKeys } from "./keys/hooks/useApiKeys";
import SvgColor from "src/components/svg-color";

const STATUS_COLORS = {
  healthy: "success",
  connected: "success", // legacy
  disconnected: "default",
  degraded: "warning",
  unreachable: "error",
};

const QUICK_LINKS = [
  {
    label: "API Keys",
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
      const res = await axiosInstance.post(endpoints.gateway.healthCheck(id));
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

  return (
    <Box p={3}>
      {/* Gateway Header */}
      <SectionHeader
        icon={GATEWAY_ICONS.overview}
        title={gateway.name}
        statusChip={
          <Chip
            label={gateway.status}
            color={STATUS_COLORS[gateway.status] || "default"}
            size="small"
          />
        }
      >
        <Button
          variant="outlined"
          size="small"
          sx={{
            borderRadius: "4px",
            height: "30px",
            px: "4px",
            width: "105px",
          }}
          onClick={() => {
            window.open("https://docs.futureagi.com/docs/command-center", "_blank");
          }}
        >
          <SvgColor
            src="/assets/icons/agent/docs.svg"
            sx={{ height: 16, width: 16, mr: 1 }}
          />
          <Typography typography="s2" fontWeight="fontWeightMedium">
            View Docs
          </Typography>
        </Button>
      </SectionHeader>

      {/* Gateway Endpoint URL */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
          >
            <Box>
              <Typography variant="caption" color="text.secondary">
                Gateway Endpoint
              </Typography>
              <Typography variant="h6" sx={{ fontFamily: "monospace" }}>
                {gateway.baseUrl}
              </Typography>
              <Typography variant="body2" color="text.secondary" mt={0.5}>
                Send OpenAI-compatible requests to this URL. Use your API key
                for authentication.
              </Typography>
            </Box>
            <Tooltip title={copied ? "Copied!" : "Copy URL"}>
              <IconButton onClick={handleCopyUrl} size="small">
                <Iconify
                  icon={copied ? "mdi:check" : "mdi:content-copy"}
                  width={20}
                />
              </IconButton>
            </Tooltip>
          </Stack>
        </CardContent>
      </Card>

      {/* Getting Started Checklist */}
      <GettingStartedCard completionState={completionState} />

      {/* KPI Cards */}
      <Grid container spacing={2} mb={3}>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardActionArea
              onClick={() => navigate("/dashboard/gateway/analytics")}
              sx={{ textAlign: "center", py: 2.5, px: 1 }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                display="block"
              >
                Requests (24h)
              </Typography>
              <Typography variant="h4" mt={0.5}>
                {Number(val(overview?.total_requests) ?? 0).toLocaleString()}
              </Typography>
            </CardActionArea>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardActionArea
              onClick={() => navigate("/dashboard/gateway/analytics/cost")}
              sx={{ textAlign: "center", py: 2.5, px: 1 }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                display="block"
              >
                Cost (24h)
              </Typography>
              <Typography variant="h4" color="primary.main" mt={0.5}>
                {formatCost(val(overview?.total_cost))}
              </Typography>
            </CardActionArea>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardActionArea
              onClick={() => navigate("/dashboard/gateway/analytics/latency")}
              sx={{ textAlign: "center", py: 2.5, px: 1 }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                display="block"
              >
                Avg Latency
              </Typography>
              <Typography variant="h4" mt={0.5}>
                {val(overview?.avg_latency_ms) != null
                  ? `${Number(val(overview.avg_latency_ms)).toFixed(0)}ms`
                  : "\u2014"}
              </Typography>
              {(() => {
                const raw = val(overview?.avg_latency_ms);
                const requests = Number(val(overview?.total_requests) ?? 0);
                if (raw == null || requests === 0) return null;
                const ms = Number(raw);
                const label = ms < 200 ? "Good" : ms < 500 ? "Fair" : "Slow";
                const color =
                  ms < 200
                    ? "success.main"
                    : ms < 500
                      ? "warning.main"
                      : "error.main";
                return (
                  <Typography variant="caption" color={color} fontWeight={600}>
                    {label}
                  </Typography>
                );
              })()}
            </CardActionArea>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardActionArea
              onClick={() => navigate("/dashboard/gateway/analytics/errors")}
              sx={{ textAlign: "center", py: 2.5, px: 1 }}
            >
              <Typography
                variant="caption"
                color="text.secondary"
                display="block"
              >
                Error Rate
              </Typography>
              <Typography
                variant="h4"
                mt={0.5}
                color={
                  Number(val(overview?.error_rate) ?? 0) > 5
                    ? "error.main"
                    : "success.main"
                }
              >
                {val(overview?.error_rate) != null
                  ? `${Number(val(overview.error_rate)).toFixed(1)}%`
                  : "0%"}
              </Typography>
            </CardActionArea>
          </Card>
        </Grid>
      </Grid>

      {/* Provider Health Summary + Quick Links */}
      <Grid container spacing={2} mb={3}>
        <Grid item xs={12} md={6}>
          <Card sx={{ height: "100%" }}>
            <CardContent>
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
                mb={2}
              >
                <Typography variant="h6">Provider Status</Typography>
                <Stack direction="row" spacing={1} alignItems="center">
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
                              : "mdi:heart-pulse"
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
                      {healthCheckMutation.isPending
                        ? "Checking..."
                        : "Health Check"}
                    </Button>
                  </Tooltip>
                </Stack>
              </Stack>
              {providerList.length > 0 ? (
                <Stack direction="row" flexWrap="wrap" gap={1}>
                  {providerList.map((p, idx) => {
                    const isHealthy =
                      p.healthy !== false && p.status !== "unhealthy";
                    return (
                      <Chip
                        key={p.name || idx}
                        label={p.name || `Provider ${idx + 1}`}
                        color={isHealthy ? "success" : "error"}
                        variant="outlined"
                        size="small"
                        sx={{ fontSize: "0.8125rem" }}
                      />
                    );
                  })}
                </Stack>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  No provider data. Run a health check to see provider status.
                </Typography>
              )}
              <Stack direction="row" spacing={3} mt={2}>
                <Box>
                  <Typography variant="caption" color="text.secondary">
                    Models
                  </Typography>
                  <Typography variant="h6">
                    {gateway.modelCount ?? 0}
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">
                    Last Check
                  </Typography>
                  <Typography variant="body2">
                    {gateway.lastHealthCheck
                      ? new Date(gateway.lastHealthCheck).toLocaleString()
                      : "Never"}
                  </Typography>
                </Box>
              </Stack>
            </CardContent>
          </Card>
        </Grid>

        <Grid item xs={12} md={6}>
          <Card sx={{ height: "100%" }}>
            <CardContent>
              <Typography variant="h6" mb={2}>
                Quick Links
              </Typography>
              <Grid container spacing={1}>
                {QUICK_LINKS.map((link) => (
                  <Grid item xs={6} sm={3} key={link.path}>
                    <Card variant="outlined" sx={{ height: "100%" }}>
                      <CardActionArea
                        onClick={() => navigate(link.path)}
                        sx={{ p: 1.5, textAlign: "center" }}
                      >
                        <Iconify
                          icon={link.icon}
                          width={24}
                          sx={{ mb: 0.5, color: "primary.main" }}
                        />
                        <Typography variant="caption" display="block">
                          {link.label}
                        </Typography>
                      </CardActionArea>
                    </Card>
                  </Grid>
                ))}
              </Grid>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
};

export default GatewayOverviewSection;
