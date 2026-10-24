/* eslint-disable react/prop-types */
import StatusBadge from "src/components/status-badge/StatusBadge";
import React, { useState, useMemo, useCallback } from "react";
import {
  Box,
  Typography,
  Stack,
  Tab,
  Tabs,
  Grid,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  Skeleton,
} from "@mui/material";
import Section from "src/components/section";
import MetricStrip from "src/components/metric-strip";
import Iconify from "src/components/iconify";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { useNavigate, useParams } from "react-router-dom";
import {
  useGatewayConfig,
  useProviderHealth,
} from "../providers/hooks/useGatewayConfig";
import { useAnalyticsOverview } from "../analytics/hooks/useAnalyticsOverview";
import { useGatewayContext } from "../context/useGatewayContext";

import CreateAlertRuleDialog from "./CreateAlertRuleDialog";
import CreateChannelDialog from "./CreateChannelDialog";
import { val } from "../utils/analyticsHelpers";
import { formatCost } from "../utils/formatters";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CHIP_ROLE = {
  success: "success",
  warning: "warning",
  error: "danger",
  info: "info",
  primary: "discovery",
  secondary: "discovery",
  default: "neutral",
};

function extractAlertRules(config) {
  const alerting = config?.alerting || config?.alerts || {};
  const rules = alerting.rules || alerting.alert_rules || [];
  if (Array.isArray(rules)) return rules;
  if (typeof rules === "object") {
    return Object.entries(rules).map(([name, cfg]) => ({
      name,
      ...(typeof cfg === "object" ? cfg : {}),
    }));
  }
  return [];
}

function extractChannels(config) {
  const alerting = config?.alerting || config?.alerts || {};
  const channels = alerting.channels || alerting.notification_channels || [];
  if (Array.isArray(channels)) return channels;
  if (typeof channels === "object") {
    return Object.entries(channels).map(([name, cfg]) => ({
      name,
      ...(typeof cfg === "object" ? cfg : {}),
    }));
  }
  return [];
}

const TAB_SLUGS = ["metrics", "rules", "channels"];

function tabSlugToIndex(slug) {
  const idx = TAB_SLUGS.indexOf(slug);
  return idx >= 0 ? idx : 0;
}

function getSeverityColor(severity) {
  if (severity === "critical") return "error";
  if (severity === "warning") return "warning";
  return "info";
}

// ---------------------------------------------------------------------------
// Alert Rules Tab
// ---------------------------------------------------------------------------

const AlertRulesTab = ({ rules }) => (
  <Section variant="panel">
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Rule</TableCell>
            <TableCell>Metric</TableCell>
            <TableCell>Condition</TableCell>
            <TableCell>Threshold</TableCell>
            <TableCell>Window</TableCell>
            <TableCell>Severity</TableCell>
            <TableCell>Status</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rules.map((rule, idx) => (
            <TableRow key={rule.name || idx}>
              <TableCell>
                <Typography variant="body2" fontWeight={500}>
                  {rule.name || `Rule ${idx + 1}`}
                </Typography>
              </TableCell>
              <TableCell>
                <Typography variant="body2">
                  {rule.metric || "\u2014"}
                </Typography>
              </TableCell>
              <TableCell>
                <Typography variant="body2">
                  {rule.condition || rule.operator || "\u2014"}
                </Typography>
              </TableCell>
              <TableCell>
                <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
                  {rule.threshold ?? "\u2014"}
                </Typography>
              </TableCell>
              <TableCell>
                <Typography variant="body2">
                  {rule.window || rule.duration || "\u2014"}
                </Typography>
              </TableCell>
              <TableCell>
                <StatusBadge
                  status={rule.severity || "info"}
                  label={String(rule.severity || "info")}
                  role={CHIP_ROLE[getSeverityColor(rule.severity)]}
                />
              </TableCell>
              <TableCell>
                <StatusBadge
                  status={rule.enabled !== false ? "Active" : "Disabled"}
                  label={String(rule.enabled !== false ? "Active" : "Disabled")}
                  role={
                    CHIP_ROLE[rule.enabled !== false ? "success" : "default"]
                  }
                />
              </TableCell>
            </TableRow>
          ))}
          {rules.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} align="center">
                <Typography variant="body2" color="text.secondary" py={4}>
                  No alert rules configured. Click &quot;Create Rule&quot; to
                  add your first alert rule.
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </TableContainer>
  </Section>
);

// ---------------------------------------------------------------------------
// Channels Tab
// ---------------------------------------------------------------------------

const CHANNEL_ICONS = {
  webhook: "solar:link-circle-linear",
  email: "solar:letter-linear",
  slack: "mdi:slack",
  pagerduty: "solar:smartphone-linear",
};

function maskUrl(url) {
  if (!url) return "\u2014";
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}/\u2026`;
  } catch {
    return url.length > 40 ? `${url.substring(0, 37)}...` : url;
  }
}

const ChannelsTab = ({ channels }) => (
  <Stack spacing={2}>
    {channels.length === 0 ? (
      <Section variant="panel" sx={{ p: 4 }}>
        <Typography color="text.secondary" textAlign="center">
          No notification channels configured.
        </Typography>
      </Section>
    ) : (
      channels.map((ch, idx) => (
        <Section variant="panel" key={ch.name || idx}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            mb={1.5}
          >
            <Stack direction="row" spacing={1.5} alignItems="center">
              <Iconify
                icon={CHANNEL_ICONS[ch.type] || "solar:bell-linear"}
                width={24}
                sx={{ color: "primary.main" }}
              />
              <Typography variant="h6">
                {ch.name || `Channel ${idx + 1}`}
              </Typography>
            </Stack>
            <Stack direction="row" spacing={0.5}>
              <Chip
                label={ch.type || "webhook"}
                color="primary"
                size="small"
                variant="outlined"
              />
              <Chip
                label={ch.enabled !== false ? "Enabled" : "Disabled"}
                color={ch.enabled !== false ? "success" : "default"}
                size="small"
              />
            </Stack>
          </Stack>
          <Stack spacing={0.75}>
            {(ch.url || ch.endpoint || ch.webhook_url) && (
              <Stack direction="row" spacing={2}>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ minWidth: 120 }}
                >
                  Endpoint
                </Typography>
                <Typography
                  variant="body2"
                  sx={{ fontFamily: "monospace", fontSize: "13px" }}
                >
                  {maskUrl(ch.url || ch.endpoint || ch.webhook_url)}
                </Typography>
              </Stack>
            )}
            {ch.email && (
              <Stack direction="row" spacing={2}>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ minWidth: 120 }}
                >
                  Email
                </Typography>
                <Typography variant="body2">{ch.email}</Typography>
              </Stack>
            )}
            {ch.channel && (
              <Stack direction="row" spacing={2}>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ minWidth: 120 }}
                >
                  Channel
                </Typography>
                <Typography variant="body2">{ch.channel}</Typography>
              </Stack>
            )}
            {ch.severity_filter && (
              <Stack direction="row" spacing={2}>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ minWidth: 120 }}
                >
                  Severity Filter
                </Typography>
                <Typography variant="body2">{ch.severity_filter}</Typography>
              </Stack>
            )}
            {ch.description && (
              <Stack direction="row" spacing={2}>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ minWidth: 120 }}
                >
                  Description
                </Typography>
                <Typography variant="body2">{ch.description}</Typography>
              </Stack>
            )}
          </Stack>
        </Section>
      ))
    )}
  </Stack>
);

// ---------------------------------------------------------------------------
// Live Metrics Tab
// ---------------------------------------------------------------------------

const LiveMetricsTab = ({
  overview,
  providerHealth,
  gatewayId: _gatewayId,
}) => {
  const navigate = useNavigate();
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

  return (
    <Stack spacing={3}>
      {/* Key metrics */}
      <MetricStrip
        items={[
          {
            label: "Request rate",
            value: Number(val(overview?.total_requests) ?? 0).toLocaleString(),
            hint: "total",
            onClick: () => navigate("/dashboard/gateway/analytics?tab=usage"),
          },
          {
            label: "Error rate",
            value:
              val(overview?.error_rate) != null
                ? `${Number(val(overview.error_rate)).toFixed(1)}%`
                : "0%",
            hint:
              Number(val(overview?.error_rate) ?? 0) > 5
                ? "Above 5%"
                : undefined,
            onClick: () => navigate("/dashboard/gateway/analytics?tab=errors"),
          },
          {
            label: "Avg latency",
            value:
              val(overview?.avg_latency_ms) != null
                ? `${Number(val(overview.avg_latency_ms)).toFixed(0)}ms`
                : "\u2014",
            onClick: () => navigate("/dashboard/gateway/analytics?tab=latency"),
          },
          {
            label: "Total cost",
            value: formatCost(val(overview?.total_cost)),
            onClick: () => navigate("/dashboard/gateway/analytics?tab=cost"),
          },
        ]}
      />

      {/* Provider status indicators */}
      <Section variant="panel">
        <Typography variant="h6" mb={2}>
          Provider Status
        </Typography>
        {providerList.length > 0 ? (
          <Stack direction="row" flexWrap="wrap" gap={1.5}>
            {providerList.map((p, idx) => {
              const isHealthy = p.healthy !== false && p.status !== "unhealthy";
              return (
                <Chip
                  key={p.name || idx}
                  label={p.name || `Provider ${idx + 1}`}
                  color={isHealthy ? "success" : "error"}
                  variant="outlined"
                />
              );
            })}
          </Stack>
        ) : (
          <Typography variant="body2" color="text.secondary">
            No provider status data available.
          </Typography>
        )}
      </Section>
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const AlertingMonitoringSection = () => {
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const tab = tabSlugToIndex(tabSlug);

  const handleTabChange = useCallback(
    (_, newIndex) => {
      if (newIndex === 0) {
        navigate("/dashboard/gateway/monitoring", { replace: true });
      } else {
        navigate(`/dashboard/gateway/monitoring/${TAB_SLUGS[newIndex]}`, {
          replace: true,
        });
      }
    },
    [navigate],
  );

  const [createRuleOpen, setCreateRuleOpen] = useState(false);
  const [createChannelOpen, setCreateChannelOpen] = useState(false);
  const { gatewayId, isLoading: gwLoading } = useGatewayContext();

  const { data: config, isLoading: configLoading } =
    useGatewayConfig(gatewayId);
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

  const rules = useMemo(() => extractAlertRules(config), [config]);
  const channels = useMemo(() => extractChannels(config), [config]);

  if (gwLoading || configLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={260} height={40} />
          <Stack direction="row" spacing={1}>
            <Skeleton width={110} height={36} variant="rounded" />
            <Skeleton width={110} height={36} variant="rounded" />
          </Stack>
        </Stack>
        <Skeleton width="50%" height={40} sx={{ mb: 3 }} />
        <Grid container spacing={2}>
          {[...Array(4)].map((_, i) => (
            <Grid item xs={6} sm={3} key={i}>
              <Skeleton variant="rounded" height={90} />
            </Grid>
          ))}
        </Grid>
      </Box>
    );
  }

  return (
    <Box p={3}>
      <SectionHeader
        sx={{ mb: 1.5 }}
        icon={GATEWAY_ICONS.monitoring}
        title="Monitoring"
        subtitle="Configure alert rules and notification channels"
        actions={[
          {
            label: "Create Rule",
            variant: "contained",
            size: "small",
            icon: "solar:add-linear",
            onClick: () => setCreateRuleOpen(true),
          },
          {
            label: "Add Channel",
            variant: "outlined",
            size: "small",
            icon: "solar:add-linear",
            onClick: () => setCreateChannelOpen(true),
          },
        ]}
      />

      <Tabs
        value={tab}
        onChange={handleTabChange}
        sx={{ mb: 1.5, borderBottom: 1, borderColor: "divider" }}
      >
        <Tab label="Live Metrics" />
        <Tab label="Alert Rules" />
        <Tab label="Channels" />
      </Tabs>

      {tab === 0 && (
        <LiveMetricsTab
          overview={overview}
          providerHealth={providerHealth}
          gatewayId={gatewayId}
        />
      )}
      {tab === 1 && <AlertRulesTab rules={rules} />}
      {tab === 2 && <ChannelsTab channels={channels} />}

      <CreateAlertRuleDialog
        open={createRuleOpen}
        onClose={() => setCreateRuleOpen(false)}
        gatewayId={gatewayId}
      />
      <CreateChannelDialog
        open={createChannelOpen}
        onClose={() => setCreateChannelOpen(false)}
        gatewayId={gatewayId}
      />
    </Box>
  );
};

export default AlertingMonitoringSection;
