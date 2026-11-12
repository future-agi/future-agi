import React, { useMemo } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";

import { GatewayEmptyState, GatewayStatusChip } from "../../components/frame";
import { useGatewayConfig } from "../../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../../context/useGatewayContext";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { formatLatency } from "../../utils/formatters";
import { useMCPStatus } from "../hooks/useMCPConfig";
import { useMCPReleaseFeatures } from "../hooks/useMCPReleaseFeatures";
import { NOT_AVAILABLE_IN_DEPLOYMENT } from "src/sections/omega/shell/agent-infrastructure-nav";
import { useMCPToolCalls } from "../hooks/useMCPToolCalls";
import { useMCPToolCallFeatures } from "../hooks/useMCPToolCallFeatures";
import { isMCPRuntimeOff } from "../hooks/mcpRuntimeState";
import RuntimeNotice from "../runtimeNotice";
import CopyField from "../components/CopyField";
import SectionTitle from "../components/SectionTitle";
import ServerMark from "../components/ServerMark";
import StatTile from "../components/StatTile";
import { outcomeOf, toolLabel } from "../calls/toolCalls";
import { mcpEndpoint } from "../virtual/virtualServers";
import { getMCPServerId, getMCPServerToolCount, plural, serverHealth, urlHost } from "../utils";
import { GATEWAY_MONO } from "../../utils/fonts";

const DAY_MS = 24 * 60 * 60 * 1000;

function Panel({ children, sx }) {
  return (
    <Box sx={{
      borderRadius: 1, border: "1px solid", borderColor: "divider",
      bgcolor: "background.paper", p: 1.5, display: "flex",
      flexDirection: "column", gap: 1.25, minWidth: 0, ...sx,
    }}>
      {children}
    </Box>
  );
}
Panel.propTypes = { children: PropTypes.node, sx: PropTypes.object };

// Configuration and call history recover independently. Retained query data
// remains useful, but an unsuccessful refresh is never a current empty result.
export default function OverviewTab({ gatewayId }) {
  const navigate = useNavigate();
  const { canEdit } = useGatewayAdmin();
  const { explorer } = useMCPToolCallFeatures(gatewayId);
  const features = useMCPReleaseFeatures(gatewayId);
  // Shown while the status loads or fails; a switch that is off says so.
  const catalog = features.shown?.catalog ?? features.catalog;
  const virtualServers =
    features.shown?.virtualServers ?? features.virtualServers;
  const virtualServersOff = features.off?.virtualServers === true;
  const { gateway } = useGatewayContext();
  const baseUrl = gateway?.baseUrl || "";
  const statusQuery = useMCPStatus(gatewayId);
  const { data: status, isLoading: statusLoading } = statusQuery;
  const runtimeOff = isMCPRuntimeOff(status);
  const configQuery = useGatewayConfig(gatewayId, { meta: { errorHandled: true } });
  const { data: config, isLoading: configLoading, isError: configError } = configQuery;
  // One page of the last day's calls: a full page is a floor ("100+").
  const since = useMemo(() => new Date(Date.now() - DAY_MS).toISOString(), []);
  const callsQuery = useMCPToolCalls(
    { limit: 100, from: since },
    { gatewayId, enabled: explorer && !runtimeOff },
  );
  const { data: callPages, isLoading: callsLoading, isError: callsError } = callsQuery;
  const mcp = config?.mcp || {};
  const configured = mcp.servers && typeof mcp.servers === "object" ? mcp.servers : {};
  const serverIds = Object.keys(configured).sort();
  const health = useMemo(() => {
    const map = {};
    (status?.servers || []).forEach((row) => {
      const id = getMCPServerId(row);
      if (id) map[id] = row;
    });
    return map;
  }, [status]);
  const healthy = serverIds.filter(
    (id) => serverHealth(health[id], configured[id]).tone === "healthy",
  ).length;
  const virtualSlugs = virtualServers ? Object.keys(mcp.virtual_servers || {}).sort() : [];
  const firstPage = callPages?.pages?.[0];
  const calls = firstPage?.results || [];
  const more = Boolean(firstPage?.next);
  const failed = calls.filter((call) => call.outcome !== "success").length;
  const empty = Boolean(config) && !configLoading && !configError && serverIds.length === 0;
  const configUnavailable = configError && !config;
  const goTab = (slug, extra = "") => navigate(`${paths.dashboard.gateway.mcp}?tab=${slug}${extra}`);
  const endpoints = config ? [
    { label: "Organization default", url: mcpEndpoint(baseUrl, "") },
    ...virtualSlugs.map((slug) => ({
      label: mcp.virtual_servers[slug]?.title || slug,
      url: mcpEndpoint(baseUrl, slug),
    })),
  ].filter((endpoint) => endpoint.url) : [];

  // The shell normally mounts SavedOverview instead; do not expose live
  // actions if this view retains a deployment-off status during a transition.
  if (runtimeOff) return <RuntimeNotice />;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {configError && (
        <Alert severity={config ? "warning" : "error"} action={
          <Button aria-label="Retry server configuration" disabled={configQuery.isFetching}
            onClick={() => configQuery.refetch()}>Retry</Button>
        }>
          {config
            ? "Server configuration is stale. The refresh failed; showing the last loaded settings and endpoints."
            : "Server configuration is unavailable. Retry to load your saved servers."}
        </Alert>
      )}
      {empty ? (
        <Box>
          <GatewayEmptyState
            icon="mcp"
            title="Connect your first MCP server"
            description={canEdit
              ? "Add a custom MCP server to make its tools available through the gateway."
              : "Ask an Owner or Admin to add an MCP server for your organization."}
            actionLabel="Add server"
            onAction={canEdit ? () => goTab("servers", "&add=1") : undefined}
          />
          {catalog && (
            <Box sx={{ display: "flex", justifyContent: "center" }}>
              <Button variant="outlined" size="small"
                onClick={() => navigate(paths.dashboard.gateway.integrations)}>
                Browse integrations
              </Button>
            </Box>
          )}
        </Box>
      ) : (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5 }}>
          <StatTile label="Servers"
            value={configUnavailable ? "Unavailable" : `${healthy} of ${serverIds.length}`}
            note={configError ? "Refresh needed" : "healthy"}
            loading={configLoading || statusLoading} />
          <StatTile label="Tools" value={Number(status?.tools || 0)}
            note={`${plural(status?.resources, "resource")}, ${plural(status?.prompts, "prompt")}`}
            loading={statusLoading} />
          {virtualServers && (
            <StatTile label="Virtual servers"
              value={configUnavailable ? "Unavailable" : virtualSlugs.length}
              note={configError ? "Refresh needed" : virtualSlugs.length ? "own endpoints" : "none"}
              loading={configLoading} />
          )}
          {virtualServersOff && !runtimeOff && (
            <StatTile label="Virtual servers" value="Unavailable"
              note={NOT_AVAILABLE_IN_DEPLOYMENT} />
          )}
          {explorer && (
            <StatTile label="Tool calls, last 24 h"
              value={callsError && !firstPage ? "Unavailable" : `${calls.length}${more ? "+" : ""}`}
              note={callsError ? "Refresh needed" : `${failed}${more ? "+" : ""} not successful`}
              loading={callsLoading} />
          )}
        </Box>
      )}
      <Box sx={{ display: "grid", gridTemplateColumns: {
        xs: "1fr", md: empty ? "1fr" : "minmax(0, 3fr) minmax(0, 2fr)",
      }, gap: 2, alignItems: "start" }}>
        {!empty && (
          <Panel>
            <SectionTitle title="Server health" actions={
              <Button size="small" variant="text" onClick={() => goTab("servers")}>All servers</Button>
            } />
            {configLoading && <Skeleton variant="rounded" height={96} />}
            {configUnavailable && <Typography typography="s2" color="text.secondary">Load server configuration to view health.</Typography>}
            {serverIds.map((id) => {
              const row = health[id];
              const verdict = serverHealth(row, configured[id]);
              return (
                <Box key={id} sx={{ display: "flex", gap: 1.25, alignItems: "flex-start",
                  py: 1, borderTop: "1px solid", borderColor: "divider" }}>
                  <ServerMark id={id} url={configured[id]?.url} />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                      <Typography typography="s1" fontWeight="fontWeightMedium">{id}</Typography>
                      <GatewayStatusChip tone={verdict.tone} label={verdict.label} />
                    </Box>
                    <Typography typography="s2" color="text.secondary" sx={{ overflowWrap: "anywhere" }}>
                      {`${urlHost(configured[id]?.url)}${row ? ` · ${getMCPServerToolCount(row)} tools` : ""}`}
                    </Typography>
                    {row?.last_error && (
                      <Typography typography="s2" color="error.main"
                        sx={{ fontFamily: GATEWAY_MONO, overflowWrap: "anywhere" }}>
                        {row.last_error}
                      </Typography>
                    )}
                  </Box>
                </Box>
              );
            })}
          </Panel>
        )}
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          {!empty && !configUnavailable && (
            <Panel>
              <SectionTitle title="Connect a client" note={
                <>
                  {"Streamable HTTP. Send a gateway key as "}
                  <Box component="code" sx={{ fontFamily: GATEWAY_MONO }}>Authorization: Bearer &lt;key&gt;</Box>
                  {". "}
                  <Link component={RouterLink} underline="hover"
                    href={`${paths.dashboard.gateway.keys}${canEdit ? "?create=1" : ""}`}>
                    {canEdit ? "Create a key" : "View existing keys"}
                  </Link>
                </>
              } />
              {endpoints.map((endpoint) => (
                <Box key={endpoint.url} sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
                  <Typography typography="s2" color="text.secondary">{endpoint.label}</Typography>
                  <CopyField value={endpoint.url} label={`${endpoint.label} endpoint`} />
                </Box>
              ))}
            </Panel>
          )}
          {explorer && (
            <Panel>
              <SectionTitle title="Recent tool calls" actions={
                <Button size="small" variant="text" onClick={() => goTab("calls")}>View all</Button>
              } />
              {callsError && (
                <Alert severity={firstPage ? "warning" : "error"} action={
                  <Button aria-label="Retry tool calls" disabled={callsQuery.isFetching}
                    onClick={() => callsQuery.refetch()}>Retry</Button>
                }>
                  {firstPage
                    ? "Tool call history is stale. The refresh failed; showing the last loaded calls."
                    : "Tool call history is unavailable. Retry to load recent calls."}
                </Alert>
              )}
              {callsLoading ? <Skeleton variant="rounded" height={96} /> : calls.length === 0 ? (
                !callsError && <Typography typography="s2" color="text.secondary">No tool call in the last 24 hours.</Typography>
              ) : calls.slice(0, 5).map((call) => {
                const outcome = outcomeOf(call.outcome);
                // A cut name or server line shows in full on hover.
                const name = toolLabel(call);
                const where = `${call.server_id} · ${formatLatency(call.latency_ms)}`;
                return (
                  <Box key={call.id} sx={{ display: "flex", alignItems: "center", gap: 1,
                    justifyContent: "space-between", py: 0.5, borderTop: "1px solid", borderColor: "divider" }}>
                    <Box sx={{ minWidth: 0 }}>
                      <Typography typography="s1" noWrap title={name}>{name}</Typography>
                      <Typography typography="s2" color="text.secondary" noWrap title={where}>
                        {where}
                      </Typography>
                    </Box>
                    <GatewayStatusChip tone={outcome.tone} label={outcome.label} />
                  </Box>
                );
              })}
            </Panel>
          )}
        </Box>
      </Box>
    </Box>
  );
}
OverviewTab.propTypes = { gatewayId: PropTypes.string };
