import React, { useMemo, useState } from "react";
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
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import axios, { endpoints } from "src/utils/axios";
import {
  CREDITS_ROUTE,
  creditsOnFromStatus,
  noUsableModelSentence,
} from "src/components/custom-model-dropdown/noUsableModel";

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
import {
  useManagedCatalog,
  useManagedModelsStatus,
} from "../models/hooks/useManagedModels";
import { modelPath } from "../models/modelLinks";
import GettingStarted from "../onboarding/GettingStarted";
import { setupReadiness } from "../onboarding/setupReadiness";
import { useRequestCount } from "../onboarding/useRequestCount";
import {
  CopyButton,
  Figure,
  GatewayHealthChip,
  SettingsPanel,
} from "../settings/ui";
import CostTotal from "../components/CostTotal";
import { overviewCost, val } from "../utils/analyticsHelpers";
import {
  BASE_URL_UNAVAILABLE,
  PLACEHOLDER_BASE_URL,
  PLACEHOLDER_REQUEST_NOTE,
  snippetBaseUrl,
} from "../utils/snippetBaseUrl";
import QuickstartCode from "./QuickstartCode";
import GatewaySnippetKey from "./GatewaySnippetKey";
import {
  configuredRouteCandidates,
  creditRouteCandidates,
  discoverySnippets,
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

// The error rate is judged against 5% only once the period has requests: "0%"
// over no requests is not healthy, and a rate the API did not give is not
// judged either (dev 4a1c6e93df).
const ERROR_RATE_LIMIT = 5;

function errorRateHint(requests, errorRate) {
  if (!requests || errorRate == null) return null;
  return errorRate > ERROR_RATE_LIMIT
    ? { text: "Above 5%", color: "status.danger.fg" }
    : { text: "Under 5%", color: "status.success.fg" };
}

function Kpis({ overview }) {
  const requests = num(val(overview?.total_requests));
  const latency = num(val(overview?.avg_latency_ms));
  const errorRate = num(val(overview?.error_rate));
  const errorHint = errorRateHint(requests, errorRate);
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
        caption={
          errorHint && (
            <Box component="span" sx={{ color: errorHint.color }}>
              {errorHint.text}
            </Box>
          )
        }
      />
    </Box>
  );
}

Kpis.propTypes = { overview: PropTypes.object };

const readFailed = (query) => Boolean(query.isError || query.error);
const readPending = (query) => Boolean(query.isLoading || query.isPending);
const currentData = (query) =>
  readFailed(query) || readPending(query) ? undefined : query.data;

function ReadPanel({ query, label, children }) {
  if (readFailed(query)) {
    return (
      <Alert
        severity="warning"
        action={
          <Button
            color="inherit"
            size="small"
            disabled={query.isFetching}
            onClick={() => query.refetch?.()}
          >
            {query.isFetching ? "Retrying…" : "Retry"}
          </Button>
        }
      >
        Could not load {label}
      </Alert>
    );
  }
  if (readPending(query)) {
    return <Skeleton aria-label={`Loading ${label}`} variant="rounded" height={64} />;
  }
  return children;
}
ReadPanel.propTypes = {
  query: PropTypes.object.isRequired,
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function creditGuidance(status, readiness) {
  if (readiness === "ready") return "Future AGI credits are available for eligible catalog models.";
  if (status?.available === false) return "Credit-funded models are not available on this deployment.";
  if (status?.enabled === false) return "Credit-funded models are turned off for this organization.";
  if (readiness === "not-ready") return "No usable credits are available. You can use your own provider account instead.";
  return "Credit availability has not been confirmed. Your own provider account is a separate setup path.";
}

// The AI Gateway's home: endpoint setup stays usable when another read fails.
export default function GatewayOverviewPage() {
  const navigate = useNavigate();
  const {
    gateway,
    gatewayId,
    isLoading,
    error,
    refreshGateways,
    hosted,
    deploymentModeConfirmed,
    retryDeploymentMode,
  } = useGatewayContext();
  const providerQuery = useProviderHealth(gatewayId);
  // Optional config reads only enrich the provider grid. They never establish
  // whether a provider was pushed or complete source readiness.
  const configQuery = useGatewayConfig(gatewayId, { meta: { errorHandled: true } });
  const keysQuery = useApiKeys(gatewayId);
  const orgQuery = useOrgConfig();
  const requestQuery = useRequestCount({ enabled: Boolean(gatewayId) });
  const managedStatus = useManagedModelsStatus();
  const providerData = currentData(providerQuery);
  const gatewayConfig = currentData(configQuery);
  const orgConfig = currentData(orgQuery);
  const readiness = setupReadiness({
    providers: providerData?.providers,
    keys: currentData(keysQuery),
    managedStatus: currentData(managedStatus),
  });
  const managedOffered = readiness.managed === "ready";
  // The catalog is read only once credits are ready: its chat models are
  // request targets for an organization without an own-key provider.
  const catalogQuery = useManagedCatalog({ enabled: managedOffered });
  const catalogModels = managedOffered ? currentData(catalogQuery)?.models : undefined;
  const [selectedModel, setSelectedModel] = useState(null);
  const [recipe, setRecipe] = useState("request");

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
  const metricsQuery = useAnalyticsOverview(range, { enabled: Boolean(gatewayId) });

  const providers = useMemo(() => providerList(providerData), [providerData]);
  const providerRows = useMemo(
    () =>
      overviewProviderRows(gatewayConfig, orgConfig, providerData, providers),
    [gatewayConfig, orgConfig, providerData, providers],
  );
  const ownCandidates = useMemo(() => configuredRouteCandidates(providers), [providers]);
  const creditCandidates = useMemo(
    () => creditRouteCandidates(catalogModels),
    [catalogModels],
  );
  // Own-key routes first; a catalog id an own provider also serves is listed once.
  const candidates = useMemo(() => {
    const own = new Set(ownCandidates.map((candidate) => candidate.id));
    return [
      ...ownCandidates,
      ...creditCandidates.filter((candidate) => !own.has(candidate.id)),
    ];
  }, [ownCandidates, creditCandidates]);
  const creditOnly = ownCandidates.length === 0 && creditCandidates.length > 0;
  // Only a measured-healthy provider gets an automatic selection. Cold-start
  // and credit users explicitly choose an example; their choice stays local.
  const choice = selectedModel === null
    ? candidates.find((candidate) => candidate.health === "healthy")
    : candidates.find((candidate) => candidate.id === selectedModel);
  const model = choice?.id;
  // The quickstart's base URL, and the Endpoint row's: the confirmed public
  // URL when a reader could call it, otherwise the marked placeholder with a
  // note (utils/snippetBaseUrl.js).
  const quickstart = snippetBaseUrl(gateway?.baseUrl, {
    hosted,
    deploymentModeConfirmed,
  });
  const quickstartUrl = quickstart.url;
  const snippets = useMemo(() => {
    if (!quickstartUrl) return [];
    if (recipe === "discover") return discoverySnippets(quickstartUrl);
    return model ? quickstartSnippets(quickstartUrl, model) : [];
  }, [quickstartUrl, model, recipe]);
  const tryModelHref = model ? modelPath(model, "try") : null;
  // Try a model opens the model's Try it tab, so it is offered only where the
  // gateway says it serves Try it. Elsewhere it is disabled beside what works
  // instead, the model menus' sentence: a gateway API key on Future AGI
  // credits when they are on, else the organization's own provider key.
  const tryItAvailable = gateway?.delegatedPlaygroundAvailable === true;
  const tryItUnavailableReason = noUsableModelSentence(
    creditsOnFromStatus(currentData(managedStatus))
      ? CREDITS_ROUTE.API_KEY
      : CREDITS_ROUTE.NONE,
  );
  const checkSetup = () => Promise.allSettled(
    [providerQuery, keysQuery, managedStatus, requestQuery].map((query) => query.refetch?.()),
  );

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

  // The Endpoint row is the address to send requests to: the confirmed public
  // address, else the marked placeholder with its note below. Never a
  // loopback, in-cluster or unconfirmed address.
  const endpointConfigured = quickstart.kind === "configured";
  const endpointUrl = endpointConfigured ? quickstartUrl : PLACEHOLDER_BASE_URL;
  // The not-reachable warning shows where the platform has an address to
  // check: the confirmed public one on a hosted deployment, the reported one
  // on a confirmed self-hosted one.
  const checkedAddress = deploymentModeConfirmed
    ? hosted
      ? endpointConfigured
        ? quickstartUrl
        : ""
      : gateway.baseUrl || ""
    : "";
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
      {!healthy && checkedAddress && (
        <Alert severity="warning" action={checkButton("Check health")}>
          The gateway is not reachable from the platform: its last health check
          reported {gateway.status || "no status"}.
        </Alert>
      )}

      <SettingsPanel
        title="Endpoint"
        // On the placeholder the note below says why and what to do; the
        // description does not say it again.
        description={
          endpointConfigured
            ? "Send OpenAI-compatible requests here with one of your API keys."
            : undefined
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
            {endpointUrl}
          </Box>
          {endpointConfigured && (
            <CopyButton text={endpointUrl} label="Copy endpoint" />
          )}
        </Stack>
        {!endpointConfigured && (
          <Alert
            severity="info"
            action={
              !deploymentModeConfirmed && retryDeploymentMode ? (
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => retryDeploymentMode()}
                >
                  Retry deployment mode
                </Button>
              ) : undefined
            }
          >
            {/* A confirmed self-hosted deployment with no public address gets
                the new-key dialog's note: nothing is missing from the
                console. */}
            {quickstart.kind === "placeholder"
              ? PLACEHOLDER_REQUEST_NOTE
              : BASE_URL_UNAVAILABLE}
            {deploymentModeConfirmed && hosted && (
              <> Ask your deployment administrator to set AGENTCC_GATEWAY_PUBLIC_URL.</>
            )}
          </Alert>
        )}
        {quickstartUrl ? (
          <Stack spacing={2} sx={{ minWidth: 0 }}>
            <Typography typography="s2" color="text.secondary" sx={{ maxWidth: "80ch" }}>
              Your gateway API key authenticates your application. A provider key connects
              your provider account; requests through it are billed by that provider.
              Do not put a provider key in these examples.
            </Typography>
            <Stack direction="row" gap={1} sx={{ flexWrap: "wrap" }} aria-label="Request recipes">
              <Button size="small" variant={recipe === "request" ? "contained" : "outlined"}
                aria-pressed={recipe === "request"} onClick={() => setRecipe("request")}>
                First request
              </Button>
              <Button size="small" variant={recipe === "discover" ? "contained" : "outlined"}
                aria-pressed={recipe === "discover"} onClick={() => setRecipe("discover")}>
                Discover models
              </Button>
            </Stack>
            {recipe === "request" && (
              <>
                {candidates.length > 0 && (
                  <TextField select fullWidth size="small" label="Configured model"
                    value={model || ""} onChange={(event) => setSelectedModel(event.target.value)}
                    SelectProps={{ native: true }} InputLabelProps={{ shrink: true }}>
                    <option value="">Choose a model</option>
                    {candidates.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.id} — {candidate.healthLabel}
                      </option>
                    ))}
                  </TextField>
                )}
                {choice ? (
                  <Typography typography="s2" color="text.secondary" role="status">
                    {choice.billing === "managed"
                      ? "Runs on Future AGI credits; each request uses your credit balance."
                      : choice.health === "healthy"
                        ? "Provider measured healthy over recent requests; this is not a model compatibility test."
                        : `${choice.healthLabel}; health is unmeasured.`}{" "}
                    {choice.compatibility === "unverified"
                      ? "Chat-shaped example; compatibility is unverified. Check the model's API requirements before running."
                      : "Chat support is reported by the model metadata; this example has not been tested."}
                  </Typography>
                ) : (
                  <Stack spacing={1}>
                    <Typography typography="s2" color="text.secondary">
                      {readFailed(providerQuery) || readPending(providerQuery)
                        ? "Configured models could not be confirmed yet. Retry the Providers panel below, or discover models with your gateway key."
                        : candidates.length
                          ? creditOnly
                            ? "Choose a credit-eligible model to prepare a chat-shaped example on Future AGI credits. Selecting and copying do not send a request."
                            : "Choose a configured model to prepare a chat-shaped example. Selecting and copying do not send a request."
                          : managedOffered
                            ? readPending(catalogQuery)
                              ? "Loading credit-eligible models…"
                              : "Credit-eligible models could not be listed. Browse models for API examples, or add and apply a provider."
                            : "No usable configured model is selected. Add and apply a provider, or browse models for API examples."}
                    </Typography>
                    <Stack direction="row" gap={2} sx={{ flexWrap: "wrap" }}>
                      <Link component={RouterLink} href={gw.providers} typography="s2">Manage providers</Link>
                      <Link component={RouterLink} href={gw.models} typography="s2">Browse models</Link>
                    </Stack>
                  </Stack>
                )}
              </>
            )}
            {recipe === "discover" && (
              <Typography typography="s2" color="text.secondary" sx={{ maxWidth: "80ch" }}>
                List models with your gateway API key. Copy an exact returned model ID,
                not its display name. The models visible to your key can differ from the catalog.
                An auto entry does not guarantee eligible routing candidates.
                Copying this recipe does not send a request.
              </Typography>
            )}
            <GatewaySnippetKey gatewayId={gatewayId} route={recipe === "request" ? choice : null}>
              {(secret) => snippets.length > 0 ? <QuickstartCode snippets={snippets} personalKey={secret} /> : null}
            </GatewaySnippetKey>
          </Stack>
        ) : null}
        {quickstartUrl && (
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
        )}
      </SettingsPanel>

      <ReadPanel query={managedStatus} label="credit availability">
        <Stack spacing={0.5}>
          <Typography typography="s2" color="text.secondary">
            {creditGuidance(currentData(managedStatus), readiness.managed)}
          </Typography>
          {managedOffered && (
            <Link component={RouterLink} href={gw.models} typography="s2" underline="hover">
              Browse credit-eligible models
            </Link>
          )}
        </Stack>
      </ReadPanel>

      <GettingStarted
        readiness={readiness}
        requestQuery={requestQuery}
        onCheckAgain={checkSetup}
        canRunConsole={canEdit}
        tryModelHref={tryModelHref || undefined}
        tryItAvailable={tryItAvailable}
        tryItUnavailableReason={tryItUnavailableReason}
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
        <ReadPanel query={metricsQuery} label="metrics">
          {metricsQuery.data ? <Kpis overview={metricsQuery.data} /> : (
            <Typography typography="s2" color="text.secondary">Metrics have not been reported.</Typography>
          )}
        </ReadPanel>
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
        <ReadPanel query={providerQuery} label="providers">
          {providerData?.providers ? (
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
          ) : (
            <Typography typography="s2" color="text.secondary">
              Provider configuration has not been reported.
            </Typography>
          )}
        </ReadPanel>
      </SettingsPanel>
    </>,
  );
}
