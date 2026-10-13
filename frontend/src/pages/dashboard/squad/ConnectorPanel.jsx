import { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Grid,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

const EMPTY = [];
const rows = (result, key) =>
  result?.available && Array.isArray(result[key]) ? result[key] : EMPTY;
const reason = (result) =>
  result?.reason ||
  "The remote service could not complete this request. Try refreshing.";
const label = (value, fallback) =>
  value?.name || value?.label || value?.id || fallback;
const failure = () => ({
  available: false,
  reason: "The remote request failed. Please try again.",
});
const requestKey = () =>
  `squad-connector-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
const canUpdate = (workforce) =>
  workforce?.available === true &&
  workforce.capabilities?.permittedActions?.includes("update");
const canUpdateAgent = (agent) => agent?.capabilities?.canEdit === true;
const LEGACY_AUTHORIZATION_TARGETS = ["organization", "agent"];
const supportedAuthorizationTargets = (status) => {
  if (status?.authorizationTargets === undefined)
    return LEGACY_AUTHORIZATION_TARGETS;
  return Array.isArray(status.authorizationTargets)
    ? [
        ...new Set(
          status.authorizationTargets.filter((value) =>
            LEGACY_AUTHORIZATION_TARGETS.includes(value),
          ),
        ),
      ]
    : EMPTY;
};
const bindingTarget = (binding) =>
  binding?.target?.kind || (binding?.agentId ? "agent" : undefined);

async function read(operation) {
  try {
    return await operation();
  } catch {
    return failure();
  }
}

import { safeAuthorizeUrl } from "./connectorSecurity";

function OAuthAuthorization({
  connectorId,
  client,
  workforce,
  onRefreshWorkforce,
  onAuthorizationTargets,
}) {
  const [status, setStatus] = useState(null);
  const [target, setTarget] = useState("");
  const [agentId, setAgentId] = useState("");
  const [handoff, setHandoff] = useState(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const targets = useMemo(
    () => (status?.available ? supportedAuthorizationTargets(status) : EMPTY),
    [status],
  );
  const selectedTarget = targets.includes(target) ? target : targets[0] || "";
  const scope = useMemo(
    () => ({
      client,
      connectorId,
      target: selectedTarget,
      agentId,
      status,
      refreshing,
    }),
    [client, connectorId, selectedTarget, agentId, status, refreshing],
  );
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const mounted = useRef(false);
  const statusRequest = useRef(0);
  const agents = rows(workforce, "agents");
  const bindings = rows(status, "bindings");
  const authorized = canUpdate(workforce);
  const currentHandoff = handoff?.scope === scope ? handoff : null;

  useEffect(() => {
    setTarget((current) =>
      targets.includes(current) ? current : targets[0] || "",
    );
    setBusy(false);
    setHandoff(null);
    onAuthorizationTargets({
      client,
      connectorId,
      targets: status?.available ? targets : EMPTY,
    });
  }, [client, connectorId, status, targets, onAuthorizationTargets]);

  useEffect(() => {
    mounted.current = true;
    let active = true;
    setStatus(null);
    setRefreshing(true);
    const request = ++statusRequest.current;
    read(() => client.getConnectorOAuth(connectorId)).then((next) => {
      if (active && request === statusRequest.current) {
        setStatus(
          next.available && next.connectorId !== connectorId
            ? {
                available: false,
                reason:
                  "The server returned authorization for a different connector.",
              }
            : next,
        );
        setRefreshing(false);
      }
    });
    return () => {
      active = false;
      mounted.current = false;
    };
  }, [client, connectorId]);

  const refresh = async () => {
    const request = ++statusRequest.current;
    setRefreshing(true);
    const next = await read(() => client.getConnectorOAuth(connectorId));
    if (!mounted.current || request !== statusRequest.current) return;
    setStatus(
      next.available && next.connectorId !== connectorId
        ? {
            available: false,
            reason:
              "The server returned authorization for a different connector.",
          }
        : next,
    );
    setRefreshing(false);
    // Provider callback is server-owned; only a subsequent read establishes its outcome.
    void onRefreshWorkforce();
  };

  const start = async (binding) => {
    if (
      !authorized ||
      status?.available !== true ||
      status.configured !== true ||
      busy ||
      refreshing
    )
      return;
    const requestedTarget = binding ? bindingTarget(binding) : selectedTarget;
    if (!targets.includes(requestedTarget)) return;
    const selectedAgent = agents.find((agent) => agent.id === agentId);
    if (
      !binding &&
      requestedTarget === "agent" &&
      !canUpdateAgent(selectedAgent)
    )
      return;
    if (binding && !bindings.some((row) => row.bindingId === binding.bindingId))
      return;
    const input = binding
      ? { bindingId: binding.bindingId }
      : requestedTarget === "agent"
        ? { agentId }
        : { target: { kind: "organization" } };
    const startedScope = scope;
    setBusy(true);
    setHandoff(null);
    const next = await read(() =>
      client.startConnectorOAuth(connectorId, input, requestKey()),
    );
    if (!mounted.current || scopeRef.current !== startedScope) return;
    setBusy(false);
    if (!next.available) {
      setHandoff({ scope, error: reason(next) });
      return;
    }
    const url = safeAuthorizeUrl(next.authorizeUrl);
    const expectedTarget =
      binding?.target ||
      (binding?.agentId ? { kind: "agent", agentId: binding.agentId } : null) ||
      (requestedTarget === "agent"
        ? { kind: "agent", agentId }
        : { kind: "organization" });
    const targetMatches =
      next.target?.kind === expectedTarget.kind &&
      (expectedTarget.kind !== "agent" ||
        next.target.agentId === expectedTarget.agentId);
    if (
      !url ||
      next.connectorId !== connectorId ||
      !targetMatches ||
      typeof next.bindingId !== "string" ||
      !next.bindingId ||
      (binding && next.bindingId !== binding.bindingId)
    ) {
      setHandoff({
        scope,
        error:
          "Remote OAuth did not return a safe HTTPS authorization URL for this selection.",
      });
      return;
    }
    setHandoff({ scope, url });
  };

  const changeTarget = (value) => {
    setTarget(value);
    setBusy(false);
    setHandoff(null);
  };
  const changeAgent = (value) => {
    setAgentId(value);
    setBusy(false);
    setHandoff(null);
  };
  return (
    <Stack spacing={1.5}>
      <Typography variant="subtitle2">Authorization</Typography>
      {refreshing ? (
        <CircularProgress size={20} aria-label="Refreshing authorization" />
      ) : null}
      {status?.available === false ? (
        <Alert severity="error">{reason(status)}</Alert>
      ) : null}
      {status?.available && !status.configured ? (
        <Alert severity="info">
          OAuth is not configured for this connector on the server.
        </Alert>
      ) : null}
      {status?.available && status.configured ? (
        <>
          <Typography variant="body2">
            {bindings.length
              ? `${bindings.length} server-confirmed installation${bindings.length === 1 ? "" : "s"}`
              : "No server-confirmed installation yet."}
          </Typography>
          {bindings.map((binding) => (
            <Card key={binding.bindingId} variant="outlined">
              <CardContent>
                <Typography variant="body2">
                  {binding.target?.kind === "organization"
                    ? "Organization installation"
                    : `Agent: ${binding.target?.agentId || binding.agentId}`}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {binding.bindingId} ·{" "}
                  {binding.state ||
                    binding.mount?.status ||
                    "Status unavailable"}
                  {binding.refresh?.status
                    ? ` · refresh: ${binding.refresh.status}`
                    : ""}
                </Typography>
                {binding.grantedScopes?.length ? (
                  <Typography variant="caption">
                    Granted scopes: {binding.grantedScopes.join(", ")}
                  </Typography>
                ) : null}
                {binding.state === "needs_attention" ||
                binding.mount?.status === "failed" ||
                binding.refresh?.status === "failed" ? (
                  <Alert severity="warning">
                    Authorization needs attention. Authorize again and refresh
                    its status.
                  </Alert>
                ) : null}
                {authorized ? (
                  <Button
                    size="small"
                    onClick={() => void start(binding)}
                    disabled={
                      busy ||
                      refreshing ||
                      !targets.includes(bindingTarget(binding))
                    }
                  >
                    Authorize again
                  </Button>
                ) : null}
              </CardContent>
            </Card>
          ))}
          {authorized ? (
            <>
              {targets.length === 0 ? (
                <Alert severity="info">
                  No authorization target is available for this selection.
                </Alert>
              ) : (
                <TextField
                  select
                  size="small"
                  label="Authorization target"
                  value={selectedTarget}
                  onChange={(event) => changeTarget(event.target.value)}
                >
                  {targets.map((value) => (
                    <MenuItem key={value} value={value}>
                      {value === "organization"
                        ? "Organization installation"
                        : "An existing agent"}
                    </MenuItem>
                  ))}
                </TextField>
              )}
              {selectedTarget === "agent" ? (
                <>
                  <TextField
                    select
                    size="small"
                    label="Agent to authorize"
                    value={agentId}
                    onChange={(event) => changeAgent(event.target.value)}
                  >
                    <MenuItem value="">Choose an agent</MenuItem>
                    {agents.filter(canUpdateAgent).map((agent) => (
                      <MenuItem key={agent.id} value={agent.id}>
                        {agent.name || agent.id}
                      </MenuItem>
                    ))}
                  </TextField>
                  <Typography variant="body2" color="text.secondary">
                    Authorize tools directly for this agent. No separate
                    organization attachment is needed.
                  </Typography>
                </>
              ) : selectedTarget === "organization" ? (
                <Typography variant="body2" color="text.secondary">
                  Install for organization data sync. Agent tool authorization
                  can be added separately.
                </Typography>
              ) : null}
              <Button
                variant="contained"
                size="small"
                disabled={
                  !targets.includes(selectedTarget) ||
                  busy ||
                  refreshing ||
                  (selectedTarget === "agent" &&
                    !agents.some(
                      (agent) => agent.id === agentId && canUpdateAgent(agent),
                    ))
                }
                onClick={() => void start()}
              >
                Connect with OAuth
              </Button>
            </>
          ) : (
            <Alert severity="info">
              An owner or administrator must authorize this connector.
            </Alert>
          )}
        </>
      ) : null}
      {currentHandoff?.error ? (
        <Alert severity="error">{currentHandoff.error}</Alert>
      ) : null}
      {currentHandoff?.url ? (
        <>
          <Button
            component="a"
            href={currentHandoff.url}
            target="_blank"
            rel="noopener noreferrer"
            variant="outlined"
          >
            Continue authorization
          </Button>
          <Alert severity="info">
            Complete authorization in the provider window, then return here and
            refresh authorization. Opening the link does not confirm an
            installation.
          </Alert>
        </>
      ) : null}
      <Button size="small" onClick={() => void refresh()} disabled={refreshing}>
        Refresh authorization
      </Button>
    </Stack>
  );
}

function AgentConnectorBindings({
  client,
  workforce,
  onRefreshWorkforce,
  organizationAuthorizationAvailable,
}) {
  const [agentId, setAgentId] = useState("");
  const [bindingId, setBindingId] = useState("");
  const [agentDetail, setAgentDetail] = useState(null);
  const [action, setAction] = useState(null);
  const scope = useMemo(
    () => ({ client, agentId, bindingId }),
    [client, agentId, bindingId],
  );
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const mounted = useRef(false);
  const agents = rows(workforce, "agents");
  const bindings = rows(workforce, "connectorBindings");
  const current = action?.scope === scope ? action : null;
  const detail = agentDetail?.agentId === agentId ? agentDetail.result : null;
  const attached = rows(
    detail?.available
      ? { ...detail, connectorBindings: detail.agent?.connectorBindings }
      : detail,
    "connectorBindings",
  );

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, [client]);
  useEffect(() => {
    let active = true;
    setAgentDetail(null);
    if (agentId)
      read(() => client.getWorkforceAgent(agentId)).then((result) => {
        if (active) setAgentDetail({ agentId, result });
      });
    return () => {
      active = false;
    };
  }, [agentId, client]);

  const attach = async () => {
    if (
      !canUpdate(workforce) ||
      !agents.some((agent) => agent.id === agentId && canUpdateAgent(agent)) ||
      !bindings.some((binding) => binding.id === bindingId) ||
      current?.busy
    )
      return;
    const startedScope = scope;
    setAction({ scope, busy: true });
    // Re-read both authority/inventory and current bindings before PATCH so a
    // stale panel cannot silently replace existing bindings or use a removed id.
    const [freshWorkforce, freshAgent] = await Promise.all([
      read(() => client.listWorkforce()),
      read(() => client.getWorkforceAgent(agentId)),
    ]);
    if (!mounted.current || startedScope !== scopeRef.current) return;
    if (
      !canUpdate(freshWorkforce) ||
      !freshAgent.available ||
      freshAgent.agent?.id !== agentId ||
      !canUpdateAgent(freshAgent.agent) ||
      !rows(freshWorkforce, "connectorBindings").some(
        (binding) => binding.id === bindingId,
      ) ||
      !Array.isArray(freshAgent.agent.connectorBindings)
    ) {
      setAction({
        scope,
        error:
          "Agent permissions or available installations changed. Refresh the workspace connections and try again.",
      });
      return;
    }
    const previous = freshAgent.agent.connectorBindings.map(
      (binding) => binding.id,
    );
    const nextIds = [...new Set([...previous, bindingId])];
    const result = await read(() =>
      client.updateWorkforceAgent(
        agentId,
        { connectorBindingIds: nextIds },
        requestKey(),
      ),
    );
    if (!mounted.current || startedScope !== scopeRef.current) return;
    if (!result.available) {
      setAction({ scope, error: reason(result) });
      return;
    }
    if (
      result.agent?.id !== agentId ||
      !nextIds.every((id) =>
        result.agent?.connectorBindings?.some((binding) => binding.id === id),
      )
    ) {
      setAction({
        scope,
        error:
          "The server did not confirm the agent's requested connections. Refresh before retrying.",
      });
      return;
    }
    setAgentDetail({ agentId, result });
    setAction({
      scope,
      success:
        "Connection attached to the agent. Deploy its updated configuration when ready.",
    });
    void onRefreshWorkforce();
  };

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={1.5}>
          <Typography variant="subtitle2">
            Attach an organization installation to an agent
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Choose from organization installations available in this workspace.
            Existing agent connections are retained.
          </Typography>
          {workforce?.available === false ? (
            <Alert severity="error">{reason(workforce)}</Alert>
          ) : null}
          {workforce?.available && !canUpdate(workforce) ? (
            <Alert severity="info">
              An owner or administrator must update agent connections.
            </Alert>
          ) : null}
          <TextField
            select
            label="Agent for workspace connection"
            size="small"
            value={agents.some((agent) => agent.id === agentId) ? agentId : ""}
            onChange={(event) => setAgentId(event.target.value)}
            disabled={!canUpdate(workforce)}
          >
            <MenuItem value="">Choose an agent</MenuItem>
            {agents.filter(canUpdateAgent).map((agent) => (
              <MenuItem key={agent.id} value={agent.id}>
                {agent.name || agent.id}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Workspace connection"
            size="small"
            value={
              bindings.some((binding) => binding.id === bindingId)
                ? bindingId
                : ""
            }
            onChange={(event) => setBindingId(event.target.value)}
            disabled={!canUpdate(workforce)}
          >
            <MenuItem value="">Choose an installation</MenuItem>
            {bindings.map((binding) => (
              <MenuItem key={binding.id} value={binding.id}>
                {binding.name || binding.id}
                {binding.provider ? ` (${binding.provider})` : ""} ·{" "}
                {binding.id}
              </MenuItem>
            ))}
          </TextField>
          {workforce?.available && bindings.length === 0 ? (
            <Typography variant="body2">
              {organizationAuthorizationAvailable
                ? "No organization installations are available. Authorize an organization installation, then refresh authorization."
                : "No shared installations are available. Authorize tools directly for an agent above."}
            </Typography>
          ) : null}
          {detail?.available === false ? (
            <Alert severity="error">{reason(detail)}</Alert>
          ) : null}
          {detail?.available ? (
            <Typography variant="body2">
              Current connections:{" "}
              {attached.length
                ? attached
                    .map((binding) => binding.name || binding.id)
                    .join(", ")
                : "None"}
            </Typography>
          ) : null}
          <Stack direction="row" spacing={1}>
            <Button
              size="small"
              variant="contained"
              disabled={
                !canUpdate(workforce) ||
                !detail?.available ||
                !canUpdateAgent(detail.agent) ||
                !bindingId ||
                !bindings.some((binding) => binding.id === bindingId) ||
                current?.busy
              }
              onClick={() => void attach()}
            >
              Attach connection
            </Button>
            <Button size="small" onClick={() => void onRefreshWorkforce()}>
              Refresh workspace connections
            </Button>
          </Stack>
          {current?.error ? (
            <Alert severity="error">{current.error}</Alert>
          ) : null}
          {current?.success ? (
            <Alert severity="success">{current.success}</Alert>
          ) : null}
        </Stack>
      </CardContent>
    </Card>
  );
}

export default function ConnectorPanel({ result, client, onRefreshWorkforce }) {
  const connectors = rows(result, "connectors");
  const [selectedId, setSelectedId] = useState("");
  const [projection, setProjection] = useState(null);
  const [workforce, setWorkforce] = useState(null);
  const [authorizationProjection, setAuthorizationProjection] = useState(null);
  const workforceRequest = useRef(0);
  const mounted = useRef(false);
  const refreshWorkforce = async () => {
    const request = ++workforceRequest.current;
    const next = await read(() => client.listWorkforce());
    if (!mounted.current || request !== workforceRequest.current) return;
    setWorkforce(next);
    if (onRefreshWorkforce) void onRefreshWorkforce();
  };
  useEffect(() => {
    mounted.current = true;
    let active = true;
    const request = ++workforceRequest.current;
    read(() => client.listWorkforce()).then((next) => {
      if (active && request === workforceRequest.current) setWorkforce(next);
    });
    return () => {
      active = false;
      mounted.current = false;
      workforceRequest.current += 1;
    };
  }, [client]);
  useEffect(() => {
    setSelectedId((current) =>
      connectors.some(
        (connector) => (connector.id || connector.connectorId) === current,
      )
        ? current
        : connectors[0]?.id || connectors[0]?.connectorId || "",
    );
  }, [connectors]);
  useEffect(() => {
    let active = true;
    setProjection(null);
    if (selectedId)
      Promise.all([
        read(() => client.getConnector(selectedId)),
        read(() => client.listConnectorSources(selectedId)),
        read(() => client.listConnectorSyncs(selectedId)),
        read(() => client.listConnectorSyncSchedules(selectedId)),
      ]).then(([detail, sources, syncs, schedules]) => {
        if (active)
          setProjection({ id: selectedId, detail, sources, syncs, schedules });
      });
    return () => {
      active = false;
    };
  }, [client, selectedId]);
  if (result?.available === false)
    return <Alert severity="info">{reason(result)}</Alert>;
  if (!connectors.length)
    return (
      <Typography color="text.secondary">
        No cloud connectors are available to this workspace.
      </Typography>
    );
  const current = projection?.id === selectedId ? projection : null;
  const connector = current?.detail?.connector || current?.detail;
  return (
    <Stack spacing={2}>
      <Grid container spacing={2}>
        <Grid item xs={12} md={4}>
          <List dense disablePadding>
            {connectors.map((item) => {
              const id = item.id || item.connectorId;
              return (
                <ListItem
                  key={id}
                  selected={id === selectedId}
                  divider
                  button
                  onClick={() => setSelectedId(id)}
                >
                  <ListItemText
                    primary={label(item, "Cloud connector")}
                    secondary={`${item.provider || item.description || "Tenant-managed integration"} · ${item.status || item.state || "available"}`}
                  />
                </ListItem>
              );
            })}
          </List>
        </Grid>
        <Grid item xs={12} md={8}>
          {!current ? (
            <CircularProgress size={20} />
          ) : current.detail?.available === false ? (
            <Alert severity="error">{reason(current.detail)}</Alert>
          ) : (
            <Stack spacing={1.5}>
              <Card variant="outlined">
                <CardContent>
                  <Stack spacing={1.5}>
                    <Typography variant="subtitle2">
                      Connector metadata
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {label(connector, "Selected connector")} · credential
                      values remain server-owned and are never displayed here.
                    </Typography>
                    <Stack direction="row" flexWrap="wrap" gap={0.75}>
                      {Object.entries(connector || {})
                        .filter(
                          ([key, value]) =>
                            [
                              "provider",
                              "status",
                              "state",
                              "description",
                              "createdAt",
                              "updatedAt",
                              "lastSyncAt",
                              "sourceCount",
                            ].includes(key) &&
                            ["string", "number", "boolean"].includes(
                              typeof value,
                            ),
                        )
                        .map(([key, value]) => (
                          <Chip
                            key={key}
                            size="small"
                            variant="outlined"
                            label={`${key}: ${String(value)}`}
                          />
                        ))}
                    </Stack>
                    {(connector?.auth ||
                      connectors.find(
                        (item) => (item.id || item.connectorId) === selectedId,
                      )?.auth) === "oauth" ? (
                      <OAuthAuthorization
                        key={selectedId}
                        connectorId={selectedId}
                        client={client}
                        workforce={workforce}
                        onRefreshWorkforce={refreshWorkforce}
                        onAuthorizationTargets={setAuthorizationProjection}
                      />
                    ) : null}
                  </Stack>
                </CardContent>
              </Card>
              <Card variant="outlined">
                <CardContent>
                  <Typography variant="subtitle2">Sources and syncs</Typography>
                  {[current.sources, current.syncs, current.schedules].some(
                    (value) => value?.available === false,
                  ) ? (
                    <Alert severity="info">
                      Some source or sync projections could not be read. Select
                      this connector again to retry.
                    </Alert>
                  ) : (
                    <Typography variant="body2">
                      {rows(current.sources, "sources").length} sources ·{" "}
                      {rows(current.syncs, "syncs").length} sync records ·{" "}
                      {rows(current.schedules, "schedules").length} schedules
                    </Typography>
                  )}
                  {rows(current.syncs, "syncs")
                    .slice(0, 8)
                    .map((sync) => (
                      <Typography key={sync.id || sync.syncId} variant="body2">
                        {label(sync, "Remote sync")} ·{" "}
                        {sync.status || sync.state}
                      </Typography>
                    ))}
                </CardContent>
              </Card>
              <Alert severity="info">
                Provider tokens and OAuth callbacks remain server-owned.
                Installation status is confirmed by the server.
              </Alert>
            </Stack>
          )}
        </Grid>
      </Grid>
      <AgentConnectorBindings
        client={client}
        workforce={workforce}
        onRefreshWorkforce={refreshWorkforce}
        organizationAuthorizationAvailable={
          authorizationProjection?.client === client &&
          authorizationProjection?.connectorId === selectedId &&
          authorizationProjection.targets.includes("organization")
        }
      />
    </Stack>
  );
}

OAuthAuthorization.propTypes = {
  connectorId: PropTypes.string.isRequired,
  client: PropTypes.object.isRequired,
  workforce: PropTypes.object,
  onRefreshWorkforce: PropTypes.func.isRequired,
  onAuthorizationTargets: PropTypes.func.isRequired,
};
AgentConnectorBindings.propTypes = {
  client: PropTypes.object.isRequired,
  workforce: PropTypes.object,
  onRefreshWorkforce: PropTypes.func.isRequired,
  organizationAuthorizationAvailable: PropTypes.bool.isRequired,
};
ConnectorPanel.propTypes = {
  result: PropTypes.object,
  client: PropTypes.object.isRequired,
  onRefreshWorkforce: PropTypes.func,
};
