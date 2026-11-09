import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import axios, { endpoints } from "src/utils/axios";

import { useAnalyticsOverview } from "../analytics/hooks/useAnalyticsOverview";
import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayPageFrame,
} from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayContext } from "../context/useGatewayContext";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { useApiKeys } from "../keys/hooks/useApiKeys";
import {
  useGatewayConfig,
  useProviderHealth,
} from "../providers/hooks/useGatewayConfig";
import { buildProviderRows } from "../providers/provider-rows";
import { useOrgConfig } from "../providers/hooks/useOrgConfig";
import { useManagedModelsStatus } from "../models/hooks/useManagedModels";
import { modelPath } from "../models/modelLinks";
import { orgModelPageId } from "../models/orgServed";
import GettingStarted from "../onboarding/GettingStarted";
import { useRequestCount } from "../onboarding/useRequestCount";
import {
  CopyButton,
  Figure,
  GatewayHealthChip,
  SettingsPanel,
} from "../settings/ui";
import CostTotal from "../components/CostTotal";
import { overviewCost, val } from "../utils/analyticsHelpers";
import QuickstartCode from "./QuickstartCode";
import GatewaySnippetKey from "./GatewaySnippetKey";
import {
  quickstartChoice,
  quickstartModel,
  quickstartSnippets,
} from "./quickstart";
import { GATEWAY_MONO } from "../utils/fonts";

const gw = paths.dashboard.gateway;

// The platform's health check of the gateway (views/gateway.py list and
// health_check): "healthy" when the gateway answered, else "unreachable".
const HEALTHY = new Set(["healthy", "connected"]);

// What a failed health check says. health_check answers 400 with result
// {status: "unreachable", error} when the gateway does not answer, and
// src/utils/axios rejects with that body.
function healthCheckFailure(err) {
  const result = err?.result;
  const reason =
    (typeof result?.error === "string" && result.error) ||
    (typeof err?.error === "string" && err.error) ||
    err?.message;
  if (result?.status === "unreachable") {
    return reason ? `Gateway unreachable: ${reason}` : "Gateway unreachable";
  }
  return reason ? `Health check failed: ${reason}` : "Health check failed";
}

const CHECK_TIP =
  "Check that the platform can reach the gateway. Providers are listed as configured, not called.";

function providerList(data) {
  const providers = data?.providers;
  if (Array.isArray(providers)) return providers;
  if (providers && typeof providers === "object") {
    return Object.entries(providers).map(([name, info]) => ({
      name,
      ...(typeof info === "object" ? info : {}),
    }));
  }
  return [];
}

const num = (value) => (value == null || value === "" ? null : Number(value));

// The Providers page's rows (providers/provider-rows.js), so both pages agree:
// the gateway config lists the providers (else the org config, else the
// providers read itself), the 24 h numbers are merged in by id, and a provider
// with no requests shows no latency or error rate.
function overviewProviderRows(config, orgConfig, providerData, providers) {
  const listed =
    config?.providers || orgConfig?.providers
      ? config
      : {
          providers: Object.fromEntries(
            providers.map((provider) => [
              provider.id || provider.name,
              provider,
            ]),
          ),
        };
  return buildProviderRows(listed, orgConfig, providerData).map((row) => ({
    id: row.id,
    name: row.displayName,
    models: row.models,
    modelCount: row.models.length,
    requests: row.requests,
    errorRate: row.errorRate,
    latency: row.avgLatencyMs,
  }));
}

// Providers with their real 24 h numbers (request count, error rate and
// latency come from the request logs). No health colour: the gateway sets
// "healthy" only through a breaker that is off in every shipped config, so
// a provider's health is not something the console can read.
const PROVIDER_COLUMNS = [
  { field: "name", headerName: "Provider", minWidth: 200, flex: 1.6 },
  {
    field: "modelCount",
    headerName: "Models",
    minWidth: 90,
    flex: 0.6,
    tooltipValueGetter: ({ data }) => (data?.models || []).join(", ") || null,
  },
  {
    field: "requests",
    headerName: "Requests (24h)",
    minWidth: 130,
    flex: 0.9,
    valueFormatter: ({ value }) =>
      value == null ? "—" : Number(value).toLocaleString(),
  },
  {
    field: "errorRate",
    headerName: "Error rate (24h)",
    minWidth: 130,
    flex: 0.9,
    valueFormatter: ({ value }) =>
      value == null ? "—" : `${Number(value).toFixed(1)}%`,
  },
  {
    field: "latency",
    headerName: "Avg latency (24h)",
    minWidth: 140,
    flex: 0.9,
    valueFormatter: ({ value }) =>
      value == null ? "—" : `${Math.round(Number(value))} ms`,
  },
];

function OverviewSkeleton() {
  return (
    <Stack spacing={2} data-testid="overview-loading">
      <Skeleton variant="rounded" height={260} />
      <Skeleton variant="rounded" height={120} />
      <Skeleton variant="rounded" height={160} />
    </Stack>
  );
}

function Kpis({ overview }) {
  const requests = num(val(overview?.total_requests));
  const latency = num(val(overview?.avg_latency_ms));
  const errorRate = num(val(overview?.error_rate));
  return (
    <Box
      data-testid="overview-kpis"
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4, minmax(0, 1fr))" },
        gap: 2,
      }}
    >
      <Figure
        label="Requests"
        value={requests == null ? "—" : requests.toLocaleString()}
      />
      <Figure
        label="Cost"
        value={overview ? <CostTotal {...overviewCost(overview)} /> : "—"}
      />
      <Figure
        label="Avg latency"
        value={latency == null || !requests ? "—" : `${Math.round(latency)} ms`}
      />
      <Figure
        label="Error rate"
        value={
          errorRate == null || !requests ? "—" : `${errorRate.toFixed(1)}%`
        }
      />
    </Box>
  );
}

Kpis.propTypes = { overview: PropTypes.object };

// The AI Gateway's home: the endpoint and a request to copy, what to set up
// next, the last 24 hours, and the providers it routes to.
export default function GatewayOverviewPage() {
  const navigate = useNavigate();
  const { gateway, gatewayId, isLoading, error, refreshGateways } =
    useGatewayContext();
  const { data: providerData } = useProviderHealth(gatewayId);
  // The Providers page shows its own error for this read; here a failed read
  // falls back to the org config or the providers read, with no toast.
  const { data: gatewayConfig } = useGatewayConfig(gatewayId, {
    meta: { errorHandled: true },
  });
  const { data: apiKeys, isLoading: keysLoading } = useApiKeys(gatewayId);
  const { data: orgConfig } = useOrgConfig();
  const { data: requestCount, isLoading: countLoading } = useRequestCount({
    enabled: Boolean(gatewayId),
  });
  // Managed offers require organization enablement, not just deployment
  // availability. Otherwise setup points at the organization's own providers.
  const managedStatus = useManagedModelsStatus();
  const managedOffered =
    managedStatus.data?.available === true &&
    managedStatus.data?.enabled === true;

  // Check health posts to the gateway: Owner or Admin only, as on Gateway
  // settings (useGatewayAdmin). Anyone else sees it disabled, with the role
  // it needs.
  const { canEdit } = useGatewayAdmin();
  const healthCheck = useMutation({
    mutationFn: async (id) => {
      const { data } = await axios.post(endpoints.gateway.healthCheck(id), {});
      return data;
    },
    onSuccess: (data) => {
      refreshGateways?.();
      const status = data?.result?.status;
      enqueueSnackbar(
        status === "healthy" ? "Gateway healthy" : "Health check complete",
        { variant: status === "healthy" ? "success" : "info" },
      );
    },
    onError: (err) => {
      refreshGateways?.();
      enqueueSnackbar(healthCheckFailure(err), { variant: "error" });
    },
  });

  // The last 24 hours, fixed for this visit. No gateway_id: the overview
  // contract has none, and the dev request guard refuses unknown params.
  const range = useMemo(() => {
    const end = new Date();
    const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
    return { start: start.toISOString(), end: end.toISOString() };
  }, []);
  const { data: overview } = useAnalyticsOverview(range, {
    enabled: Boolean(gatewayId),
  });

  const providers = useMemo(() => providerList(providerData), [providerData]);
  const providerRows = useMemo(
    () =>
      overviewProviderRows(gatewayConfig, orgConfig, providerData, providers),
    [gatewayConfig, orgConfig, providerData, providers],
  );
  const model = quickstartModel(providers);
  const snippets = useMemo(
    () => quickstartSnippets(gateway?.baseUrl, model),
    [gateway?.baseUrl, model],
  );
  // Get started's Try a model: the quickstart's model on its model page's Try
  // it tab, "<provider>/<model>", the id the gateway routes to that provider.
  // None when no provider of the org serves a model.
  const choice = quickstartChoice(providers);
  const tryModelId =
    choice && orgModelPageId(providers, choice.provider, choice.model);
  const tryModelHref = tryModelId ? modelPath(tryModelId, "try") : null;

  const frame = (content) => (
    <GatewayPageFrame
      title="Overview"
      documentTitle="AI Gateway"
      description="Your AI Gateway: the endpoint to call, and the last 24 hours."
      actions={
        <Button
          variant="outlined"
          href="https://docs.futureagi.com/docs/command-center"
          target="_blank"
          rel="noopener noreferrer"
        >
          Docs
        </Button>
      }
    >
      {content}
    </GatewayPageFrame>
  );

  if (isLoading) return frame(<OverviewSkeleton />);

  if (error || !gateway) {
    return frame(
      <GatewayEmptyState
        icon="gateway"
        title="Couldn't reach the AI Gateway"
        description={
          error?.message ||
          "No gateway answered for this organization. It may still be starting."
        }
        actionLabel="Retry"
        onAction={refreshGateways}
      />,
    );
  }

  const baseUrl = gateway.baseUrl || "";
  const healthy = HEALTHY.has(gateway.status);
  const checkButton = (label) => (
    <GatewayAdminOnly allowed={canEdit}>
      <Button
        size="small"
        color="inherit"
        onClick={() => healthCheck.mutate(gatewayId)}
        disabled={!canEdit || !gatewayId || healthCheck.isPending}
      >
        {healthCheck.isPending ? "Checking…" : label}
      </Button>
    </GatewayAdminOnly>
  );

  return frame(
    <>
      {!healthy && baseUrl && (
        <Alert severity="warning" action={checkButton("Check health")}>
          The gateway is not reachable from the platform: its last health check
          reported {gateway.status || "no status"}.
        </Alert>
      )}

      <SettingsPanel
        title="Endpoint"
        description={
          baseUrl
            ? "Send OpenAI-compatible requests here with one of your API keys."
            : "Ask your deployment administrator to set AGENTCC_GATEWAY_PUBLIC_URL."
        }
        testId="overview-endpoint"
        actions={
          <Stack direction="row" alignItems="center" gap={1}>
            <GatewayHealthChip status={gateway.status} />
            {healthy &&
              (canEdit ? (
                <Tooltip title={CHECK_TIP} arrow>
                  <span>{checkButton("Check health")}</span>
                </Tooltip>
              ) : (
                checkButton("Check health")
              ))}
          </Stack>
        }
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ minWidth: 0 }}
        >
          <Box
            component="code"
            sx={{
              fontFamily: GATEWAY_MONO,
              typography: "m3",
              overflowWrap: "anywhere",
              minWidth: 0,
            }}
          >
            {baseUrl || "Gateway URL not configured"}
          </Box>
          {baseUrl && <CopyButton text={baseUrl} label="Copy endpoint" />}
        </Stack>
        {baseUrl && (
          <>
            <GatewaySnippetKey gatewayId={gatewayId}>
              {(secret) => (
                <QuickstartCode snippets={snippets} personalKey={secret} />
              )}
            </GatewaySnippetKey>
            <Typography typography="s2" color="text.secondary">
              Set{" "}
              <Box component="code" sx={{ fontFamily: GATEWAY_MONO }}>
                AGENTCC_API_KEY
              </Box>{" "}
              to one of your API keys.{" "}
              <Link
                component={RouterLink}
                href={`${gw.keys}?create=1`}
                underline="hover"
              >
                Create a key
              </Link>
            </Typography>
          </>
        )}
      </SettingsPanel>

      <GettingStarted
        loading={keysLoading || countLoading || managedStatus.isLoading}
        providerCount={providers.length}
        managedOn={Boolean(orgConfig?.managed_models?.enabled)}
        managedOffered={managedOffered}
        keyCount={Array.isArray(apiKeys) ? apiKeys.length : 0}
        requestCount={requestCount || 0}
        tryModelHref={tryModelHref || undefined}
      />

      <SettingsPanel
        title="Last 24 hours"
        description="Every request through this gateway, from the request logs."
        actions={
          <Link
            component={RouterLink}
            href={gw.analytics}
            typography="s2"
            underline="hover"
          >
            Open analytics
          </Link>
        }
      >
        <Kpis overview={overview} />
      </SettingsPanel>

      <SettingsPanel
        title="Providers"
        description="Where the gateway sends requests for your own keys. Live errors are in Monitoring."
        actions={
          <Stack direction="row" gap={2}>
            <Link
              component={RouterLink}
              href={gw.monitoring}
              typography="s2"
              underline="hover"
            >
              Monitoring
            </Link>
            <Link
              component={RouterLink}
              href={gw.providers}
              typography="s2"
              underline="hover"
            >
              Manage providers
            </Link>
          </Stack>
        }
      >
        <GatewayGrid
          rowData={providerRows}
          columnDefs={PROVIDER_COLUMNS}
          stateKey="gateway-overview-providers"
          getRowId={(params) => String(params.data.id)}
          height={Math.max(providerRows.length, 1) * 40 + 42}
          tooltipShowDelay={300}
          emptyState={{
            icon: "providers",
            title: "No providers yet",
            description: managedOffered
              ? "Add your own provider keys, or use managed models from the catalog."
              : "Add your own provider keys; the gateway routes requests through them.",
            actionLabel: "Add a provider",
            onAction: () => navigate(gw.providers),
          }}
        />
      </SettingsPanel>
    </>,
  );
}
