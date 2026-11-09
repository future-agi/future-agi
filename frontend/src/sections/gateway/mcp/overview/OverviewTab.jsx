import React, { useMemo } from "react";
import PropTypes from "prop-types";
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
import { formatLatency } from "../../utils/formatters";
import { useMCPStatus } from "../hooks/useMCPConfig";
import { useMCPToolCalls } from "../hooks/useMCPToolCalls";
import { useMCPToolCallFeatures } from "../hooks/useMCPToolCallFeatures";
import CopyField from "../components/CopyField";
import SectionTitle from "../components/SectionTitle";
import ServerMark from "../components/ServerMark";
import StatTile from "../components/StatTile";
import { outcomeOf, toolLabel } from "../calls/toolCalls";
import { mcpEndpoint } from "../virtual/virtualServers";
import {
  getMCPServerId,
  getMCPServerToolCount,
  plural,
  serverHealth,
  urlHost,
} from "../utils";
import { GATEWAY_MONO } from "../../utils/fonts";

const DAY_MS = 24 * 60 * 60 * 1000;

function Panel({ children, sx }) {
  return (
    <Box
      sx={{
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        p: 1.5,
        display: "flex",
        flexDirection: "column",
        gap: 1.25,
        minWidth: 0,
        ...sx,
      }}
    >
      {children}
    </Box>
  );
}

Panel.propTypes = { children: PropTypes.node, sx: PropTypes.object };

// The MCP overview: what is connected and healthy, what was called in the
// last day, and the endpoints a client connects to.
export default function OverviewTab({ gatewayId }) {
  const navigate = useNavigate();
  const { explorer } = useMCPToolCallFeatures(gatewayId);
  const { gateway } = useGatewayContext();
  const baseUrl = gateway?.baseUrl || "";
  const { data: status, isLoading: statusLoading } = useMCPStatus(gatewayId);
  const { data: config, isLoading: configLoading } =
    useGatewayConfig(gatewayId);
  // One page of the last day's calls: a full page is a floor ("100+").
  const since = useMemo(() => new Date(Date.now() - DAY_MS).toISOString(), []);
  const { data: callPages, isLoading: callsLoading } = useMCPToolCalls(
    { limit: 100, from: since },
    { gatewayId, enabled: explorer },
  );

  const mcp = config?.mcp || {};
  const configured =
    mcp.servers && typeof mcp.servers === "object" ? mcp.servers : {};
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
  const virtualSlugs = Object.keys(mcp.virtual_servers || {}).sort();

  const firstPage = callPages?.pages?.[0];
  const calls = firstPage?.results || [];
  const more = Boolean(firstPage?.next);
  const failed = calls.filter((call) => call.outcome !== "success").length;

  const goTab = (slug, extra = "") =>
    navigate(`${paths.dashboard.gateway.mcp}?tab=${slug}${extra}`);

  if (!configLoading && serverIds.length === 0) {
    return (
      <GatewayEmptyState
        icon="mcp"
        title="Connect your first MCP server"
        description="Add an MCP server's URL and the gateway serves its tools to your agents through one endpoint, with keys, virtual servers and a record of every call."
        actionLabel="Add server"
        onAction={() => goTab("servers", "&add=1")}
      />
    );
  }

  const endpoints = [
    { label: "Organization default", url: mcpEndpoint(baseUrl, "") },
    ...virtualSlugs.map((slug) => ({
      label: mcp.virtual_servers[slug]?.title || slug,
      url: mcpEndpoint(baseUrl, slug),
    })),
  ].filter((endpoint) => endpoint.url);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1.5 }}>
        <StatTile
          label="Servers"
          value={`${healthy} of ${serverIds.length}`}
          note="healthy"
          loading={configLoading || statusLoading}
        />
        <StatTile
          label="Tools"
          value={Number(status?.tools || 0)}
          note={`${plural(status?.resources, "resource")}, ${plural(status?.prompts, "prompt")}`}
          loading={statusLoading}
        />
        <StatTile
          label="Virtual servers"
          value={virtualSlugs.length}
          note={virtualSlugs.length ? "own endpoints" : "none yet"}
          loading={configLoading}
        />
        {explorer && (
          <StatTile
            label="Tool calls, last 24 h"
            value={`${calls.length}${more ? "+" : ""}`}
            note={`${failed}${more ? "+" : ""} not successful`}
            loading={callsLoading}
          />
        )}
      </Box>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "1fr",
            md: "minmax(0, 3fr) minmax(0, 2fr)",
          },
          gap: 2,
          alignItems: "start",
        }}
      >
        <Panel>
          <SectionTitle
            title="Server health"
            actions={
              <Button
                size="small"
                variant="text"
                onClick={() => goTab("servers")}
              >
                All servers
              </Button>
            }
          />
          {serverIds.map((id) => {
            const row = health[id];
            const verdict = serverHealth(row, configured[id]);
            return (
              <Box
                key={id}
                sx={{
                  display: "flex",
                  gap: 1.25,
                  alignItems: "flex-start",
                  py: 1,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <ServerMark id={id} url={configured[id]?.url} />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 1,
                      flexWrap: "wrap",
                    }}
                  >
                    <Typography typography="s1" fontWeight="fontWeightMedium">
                      {id}
                    </Typography>
                    <GatewayStatusChip
                      tone={verdict.tone}
                      label={verdict.label}
                    />
                  </Box>
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    sx={{ overflowWrap: "anywhere" }}
                  >
                    {`${urlHost(configured[id]?.url)}${row ? ` · ${getMCPServerToolCount(row)} tools` : ""}`}
                  </Typography>
                  {row?.last_error && (
                    <Typography
                      typography="s2"
                      color="error.main"
                      sx={{
                        fontFamily: GATEWAY_MONO,
                        overflowWrap: "anywhere",
                      }}
                    >
                      {row.last_error}
                    </Typography>
                  )}
                </Box>
              </Box>
            );
          })}
        </Panel>

        <Box
          sx={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}
        >
          <Panel>
            <SectionTitle
              title="Connect a client"
              note={
                <>
                  {"Streamable HTTP. Send a gateway key as "}
                  <Box component="code" sx={{ fontFamily: GATEWAY_MONO }}>
                    Authorization: Bearer &lt;key&gt;
                  </Box>
                  {". "}
                  <Link
                    component={RouterLink}
                    href={`${paths.dashboard.gateway.keys}?create=1`}
                    underline="hover"
                  >
                    Create a key
                  </Link>
                </>
              }
            />
            {endpoints.map((endpoint) => (
              <Box
                key={endpoint.url}
                sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}
              >
                <Typography typography="s2" color="text.secondary">
                  {endpoint.label}
                </Typography>
                <CopyField
                  value={endpoint.url}
                  label={`${endpoint.label} endpoint`}
                />
              </Box>
            ))}
          </Panel>

          {explorer && (
            <Panel>
              <SectionTitle
                title="Recent tool calls"
                actions={
                  <Button
                    size="small"
                    variant="text"
                    onClick={() => goTab("calls")}
                  >
                    View all
                  </Button>
                }
              />
              {callsLoading ? (
                <Skeleton variant="rounded" height={96} />
              ) : calls.length === 0 ? (
                <Typography typography="s2" color="text.secondary">
                  No tool call in the last 24 hours.
                </Typography>
              ) : (
                calls.slice(0, 5).map((call) => {
                  const outcome = outcomeOf(call.outcome);
                  return (
                    <Box
                      key={call.id}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        justifyContent: "space-between",
                        py: 0.5,
                        borderTop: "1px solid",
                        borderColor: "divider",
                      }}
                    >
                      <Box sx={{ minWidth: 0 }}>
                        <Typography typography="s1" noWrap>
                          {toolLabel(call)}
                        </Typography>
                        <Typography
                          typography="s2"
                          color="text.secondary"
                          noWrap
                        >
                          {`${call.server_id} · ${formatLatency(call.latency_ms)}`}
                        </Typography>
                      </Box>
                      <GatewayStatusChip
                        tone={outcome.tone}
                        label={outcome.label}
                      />
                    </Box>
                  );
                })
              )}
            </Panel>
          )}
        </Box>
      </Box>
    </Box>
  );
}

OverviewTab.propTypes = {
  gatewayId: PropTypes.string,
};
