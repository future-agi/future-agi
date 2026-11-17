import { useMCPReleaseFeatures } from "../hooks/useMCPReleaseFeatures";
import { NOT_AVAILABLE_IN_DEPLOYMENT } from "src/sections/omega/shell/agent-infrastructure-nav";
import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import { paths } from "src/routes/paths";

import { GatewayDetailDrawer, GatewayStatusChip } from "../../components/frame";
import ConfirmDialog from "../../components/ConfirmDialog";
import { formatDateTime, formatLatency } from "../../utils/formatters";
import {
  useMCPPrompts,
  useMCPResources,
  useRemoveMCPServer,
} from "../hooks/useMCPConfig";
import { useMCPToolCalls } from "../hooks/useMCPToolCalls";
import { useMCPToolCallFeatures } from "../hooks/useMCPToolCallFeatures";
import KeyValueList from "../components/KeyValueList";
import SectionTitle from "../components/SectionTitle";
import ServerMark from "../components/ServerMark";
import { outcomeOf, toolLabel } from "../calls/toolCalls";
import { plural } from "../utils";
import { GATEWAY_MONO } from "../../utils/fonts";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import ServedToolsPanel from "./ServedToolsPanel";
import RuntimeNotice from "../runtimeNotice";

function Section({ title, note, actions, children }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <SectionTitle
        title={title}
        note={note}
        actions={actions}
        component="h3"
      />
      {children}
    </Box>
  );
}

Section.propTypes = {
  title: PropTypes.string.isRequired,
  note: PropTypes.node,
  actions: PropTypes.node,
  children: PropTypes.node,
};

function Rows({ children }) {
  return (
    <Box
      sx={{
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        "& > *:not(:last-child)": {
          borderBottom: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      {children}
    </Box>
  );
}

Rows.propTypes = { children: PropTypes.node };

function Row({ primary, secondary, end }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.25,
        py: 0.75,
        minWidth: 0,
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography typography="s1" sx={{ overflowWrap: "anywhere" }}>
          {primary}
        </Typography>
        {secondary && (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ overflowWrap: "anywhere" }}
          >
            {secondary}
          </Typography>
        )}
      </Box>
      {end}
    </Box>
  );
}

Row.propTypes = {
  primary: PropTypes.node,
  secondary: PropTypes.node,
  end: PropTypes.node,
};

// The tools the server itself lists, from the same inventory as the server
// card's count, before virtual-server and key policy. Served tools, below,
// is what clients of an endpoint get.
function ServerTools({ tools, loading, error }) {
  const count = loading || error ? "" : ` (${tools.length})`;
  return (
    <Box
      component="section"
      aria-label="Tools on this server"
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <SectionTitle
        title={`Tools on this server${count}`}
        note="The tools this server lists. Served tools, below, shows what clients get."
        component="h3"
      />
      {loading ? (
        <Typography role="status" typography="s2">
          Loading this server&apos;s tools
        </Typography>
      ) : error ? (
        <Typography typography="s2" color="text.secondary">
          This server&apos;s tools could not be loaded.
        </Typography>
      ) : tools.length === 0 ? (
        <Typography typography="s2" color="text.secondary">
          This server lists no tools yet.
        </Typography>
      ) : (
        <Rows>
          {tools.map((tool) => (
            <Row
              key={tool.name}
              primary={tool.upstream_name || tool.name}
              secondary={tool.description}
              end={
                tool.quarantined ? (
                  <GatewayStatusChip tone="warning" label="Quarantined" />
                ) : undefined
              }
            />
          ))}
        </Rows>
      )}
    </Box>
  );
}

ServerTools.propTypes = {
  tools: PropTypes.array.isRequired,
  loading: PropTypes.bool,
  error: PropTypes.bool,
};

function authText(auth) {
  if (auth.kind === "none") return "None";
  const states = {
    set: "stored",
    not_set: "not set",
    revoked: "revoked",
    expired: "expired",
    needs_reauth: "needs re-entry",
  };
  const header = auth.header ? `, header ${auth.header}` : "";
  return `${auth.label} (${states[auth.state] || auth.state}${header})`;
}

// One server: where it is, how the gateway reaches it, what it serves and
// what was called on it lately. Edit, remove, try a tool.
export default function ServerDrawer({
  row,
  gatewayId,
  onClose,
  onEdit,
  canEdit = true,
  runtimeOff = false,
  tools,
  toolsLoading = false,
  toolsError = false,
}) {
  const navigate = useNavigate();
  const { explorer } = useMCPToolCallFeatures(gatewayId);
  const features = useMCPReleaseFeatures(gatewayId);
  // Shown unless the vault switch is off, which disables it with the line.
  const credentialVault =
    features.shown?.credentialVault ?? features.credentialVault;
  const credentialVaultOff = features.off?.credentialVault === true;
  const [confirmRemove, setConfirmRemove] = useState(false);
  const removeMutation = useRemoveMCPServer();
  const { data: resources } = useMCPResources(gatewayId, {
    enabled: !runtimeOff,
  });
  const { data: prompts } = useMCPPrompts(gatewayId, { enabled: !runtimeOff });
  const serverId = row?.id;
  const { data: callPages, isLoading: callsLoading } = useMCPToolCalls(
    { server_id: serverId, limit: 10 },
    { gatewayId, enabled: explorer && Boolean(serverId) && !runtimeOff },
  );

  const own = (list) =>
    (list || []).filter((item) => item.server_id === serverId);
  const serverResources = own(resources);
  const serverTools = own(tools);
  const serverPrompts = own(prompts);
  const calls = callPages?.pages?.[0]?.results || [];
  const mcpUrl = paths.dashboard.gateway.mcp;

  const remove = () => {
    if (!canEdit || !serverId || removeMutation.isPending) return;
    removeMutation.mutate(
      { gatewayId, serverId },
      {
        onSuccess: () => {
          enqueueSnackbar(`Server "${serverId}" removed`, {
            variant: "success",
          });
          setConfirmRemove(false);
          onClose();
        },
        onError: () => {
          enqueueSnackbar("Failed to remove server", { variant: "error" });
          setConfirmRemove(false);
        },
      },
    );
  };

  return (
    <GatewayDetailDrawer
      open={Boolean(row)}
      onClose={onClose}
      title={serverId || ""}
      subtitle="MCP server"
      width={600}
    >
      {row && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1.25,
              flexWrap: "wrap",
            }}
          >
            <ServerMark id={row.id} url={row.url} size={40} />
            <GatewayStatusChip
              tone={row.health.tone}
              label={row.health.label}
            />
            <Box
              sx={{
                display: "flex",
                gap: 1,
                ml: { sm: "auto" },
                flexWrap: "wrap",
              }}
            >
              <GatewayAdminOnly allowed={canEdit}>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => onEdit(row)}
                  disabled={!canEdit}
                >
                  Edit
                </Button>
              </GatewayAdminOnly>
              <GatewayAdminOnly allowed={canEdit}>
                <Button
                  size="small"
                  variant="outlined"
                  color="error"
                  onClick={() => setConfirmRemove(true)}
                  disabled={!canEdit}
                >
                  Remove
                </Button>
              </GatewayAdminOnly>
            </Box>
          </Box>

          {row.lastError && (
            <Typography
              typography="s2"
              color="error.main"
              sx={{ fontFamily: GATEWAY_MONO, overflowWrap: "anywhere" }}
            >
              {`Last error: ${row.lastError}`}
            </Typography>
          )}

          <KeyValueList
            rows={[
              { label: "URL", value: row.url || "—", mono: true },
              { label: "Transport", value: row.transport },
              { label: "Protocol", value: row.era || undefined },
              { label: "Authentication", value: authText(row.auth) },
              {
                label: "Timeout",
                value: row.config.timeout_ms
                  ? `${row.config.timeout_ms} ms`
                  : undefined,
              },
              {
                label: "Tool list refreshed",
                value: row.lastRefreshed
                  ? formatDateTime(row.lastRefreshed)
                  : undefined,
              },
            ]}
          />

          {!runtimeOff && (
            <ServerTools
              tools={serverTools}
              loading={toolsLoading}
              error={toolsError}
            />
          )}

          {runtimeOff ? (
            <RuntimeNotice />
          ) : canEdit ? (
            <ServedToolsPanel
              key={serverId}
              gatewayId={gatewayId}
              serverId={serverId}
            />
          ) : (
            <Typography typography="s2" color="text.secondary">
              Live served-tool inventory is available to organization Owners and
              Admins.
            </Typography>
          )}

          {!runtimeOff &&
            (serverResources.length > 0 || serverPrompts.length > 0) && (
              <Section
                title="Resources and prompts"
                note={`${plural(serverResources.length, "resource")}, ${plural(serverPrompts.length, "prompt")}`}
              >
                <Rows>
                  {serverResources.map((resource) => (
                    <Row
                      key={resource.uri}
                      primary={resource.name || resource.uri}
                      secondary={resource.uri}
                      end={
                        <Typography typography="s2" color="text.secondary">
                          Resource
                        </Typography>
                      }
                    />
                  ))}
                  {serverPrompts.map((prompt) => (
                    <Row
                      key={prompt.name}
                      primary={prompt.name}
                      secondary={prompt.description}
                      end={
                        <Typography typography="s2" color="text.secondary">
                          Prompt
                        </Typography>
                      }
                    />
                  ))}
                </Rows>
              </Section>
            )}

          {!runtimeOff && explorer && (
            <Section
              title="Recent tool calls"
              actions={
                <Button
                  size="small"
                  variant="text"
                  onClick={() =>
                    navigate(
                      `${mcpUrl}?tab=calls&server=${encodeURIComponent(row.id)}`,
                    )
                  }
                >
                  View all
                </Button>
              }
            >
              {callsLoading ? (
                <Skeleton variant="rounded" height={80} />
              ) : calls.length === 0 ? (
                <Typography typography="s2" color="text.secondary">
                  No tool calls recorded for this server.
                </Typography>
              ) : (
                <Rows>
                  {calls.map((call) => {
                    const outcome = outcomeOf(call.outcome);
                    return (
                      <Row
                        key={call.id}
                        primary={toolLabel(call)}
                        secondary={`${formatDateTime(call.started_at)} · ${formatLatency(call.latency_ms)}`}
                        end={
                          <GatewayStatusChip
                            tone={outcome.tone}
                            label={outcome.label}
                          />
                        }
                      />
                    );
                  })}
                </Rows>
              )}
            </Section>
          )}

          {!runtimeOff && (credentialVault || credentialVaultOff) && (
            <Box>
              <Button
                size="small"
                variant="text"
                disabled={credentialVaultOff}
                aria-describedby={
                  credentialVaultOff ? "mcp-manage-credentials-off" : undefined
                }
                onClick={() => navigate(`${mcpUrl}?tab=credentials`)}
              >
                Manage credentials
              </Button>
              {credentialVaultOff && (
                <Typography
                  id="mcp-manage-credentials-off"
                  typography="s2"
                  color="text.secondary"
                >
                  {NOT_AVAILABLE_IN_DEPLOYMENT}
                </Typography>
              )}
            </Box>
          )}
        </Box>
      )}

      <ConfirmDialog
        open={confirmRemove}
        onClose={() => setConfirmRemove(false)}
        onConfirm={remove}
        title="Remove MCP server"
        message={`Remove "${serverId}" from the saved server configuration and revoke its stored credentials?`}
        confirmLabel="Remove server"
        isLoading={removeMutation.isPending}
      />
    </GatewayDetailDrawer>
  );
}

ServerDrawer.propTypes = {
  runtimeOff: PropTypes.bool,
  // The organization's tool inventory (useMCPTools), with its load state.
  tools: PropTypes.array,
  toolsLoading: PropTypes.bool,
  toolsError: PropTypes.bool,
  // Owner or Admin (useGatewayAdmin): only they may edit or remove a server.
  canEdit: PropTypes.bool,
  row: PropTypes.object,
  gatewayId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
  onEdit: PropTypes.func.isRequired,
};
