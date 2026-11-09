import React, { useCallback, useEffect, useMemo, useState } from "react";
import Button from "@mui/material/Button";
import { useNavigate, useSearchParams } from "react-router-dom";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { GatewayEmptyState, GatewayPageFrame } from "../components/frame";
import { useGatewayContext } from "../context/useGatewayContext";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { useMCPStatus } from "../mcp/hooks/useMCPConfig";
import MCPOffState from "../mcp/MCPOffState";
import { isMCPTurnedOff } from "../mcp/utils";
import { GatewayIcon } from "../shell/gateway-icons";
import {
  authOptions as authOptionsOf,
  connectableApps,
  filterApps,
  isConnected,
} from "./catalog-model";
import AppDetailDrawer from "./components/AppDetailDrawer";
import IntegrationsFilterBar from "./components/IntegrationsFilterBar";
import IntegrationsGrid from "./components/IntegrationsGrid";
import MyConnections from "./components/MyConnections";
import { useConnectorCatalog } from "./hooks/useConnectorCatalog";
import { useMyConnections } from "./hooks/useMyConnections";
import { ownAppRoutes } from "./hooks/useOwnOAuthApp";

const INITIAL_FILTERS = { view: "all", search: "", category: null, auth: null };
const APP_PARAM = "app";
// Where a gateway connect link lands: ?connect=<server_id>&binding=<sealed>
// (the API's console_connect_url).
const CONNECT_PARAM = "connect";
const BINDING_PARAM = "binding";
const MCP_SERVERS_HREF = `${paths.dashboard.gateway.mcp}?tab=servers`;
const MCP_OFF_DESCRIPTION =
  "Apps connect through the gateway's MCP, so none can be connected here.";

// Why a catalog read failed, as one sentence: the API's own message, else
// axios', else none.
function failureText(error) {
  const reason =
    (typeof error?.message === "string" && error.message.trim()) ||
    (typeof error?.detail === "string" && error.detail.trim()) ||
    "";
  if (!reason) return "The connector catalog did not load.";
  return `The connector catalog did not load: ${reason}${
    /[.!?]$/.test(reason) ? "" : "."
  }`;
}

// Providers, Tools & Keys > Integrations (CONNECTORS-PLAN 2.10, CN21), on
// the gateway page frame: every app the org can add from the connector
// catalog on this build (one shared API key or token, or no key), plus Custom
// MCP on the MCP page's Servers tab. One view: the filter bar, one grid of
// apps, one empty state, and the gateway detail drawer to connect an app
// with its key and read its access control. Connecting and disconnecting
// change the org's gateway config: Owner or Admin only (useGatewayAdmin,
// the catalog API's IsOrgGatewayAdmin); every read stays open.
//
// Every app connects through the gateway's MCP. On a deployment that runs
// with MCP off the gateway has no MCP route, so an add could only fail: the
// page then shows the MCP page's off state, with no app, Connect or Custom
// MCP action. A status that cannot be read is no such state (an outage), and
// until the status is known the page shows neither apps nor "no apps".
//
// Above the apps, "Your accounts" (MyConnections) lists the reader's own
// account on each of the organization's per-person MCP servers, for every
// member whatever their role. It shows when there is one, or when a gateway
// connect link landed here; a link only marks its server's row, and a
// sign-in starts only when the reader chooses Connect.
export default function IntegrationsSection() {
  const navigate = useNavigate();
  const { canEdit } = useGatewayAdmin();
  // GatewayGuard renders this page only once the gateway id is known.
  const { gatewayId } = useGatewayContext();
  const {
    data: mcpStatus,
    isPending: statusPending,
    isError: statusError,
  } = useMCPStatus(gatewayId);
  const mcpOff = !statusError && isMCPTurnedOff(mcpStatus);
  const upstreamOAuth =
    !statusError && mcpStatus?.features?.upstream_oauth === true;
  const k2Apps = !statusError && mcpStatus?.features?.k2_apps === true;
  const {
    data,
    isLoading: catalogLoading,
    isError,
    error,
    refetch,
  } = useConnectorCatalog({ enabled: !mcpOff, upstreamOAuth, k2Apps });
  const isLoading = catalogLoading || statusPending;
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [openId, setOpenId] = useState(null);
  // A link may name one app (?app=<id>): where a sign-in returns, or the
  // link an agent's connect prompt carries. It opens that app until closed.
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedId = searchParams.get(APP_PARAM);
  // A connect link's binding is kept for this visit only and dropped from
  // the address at once, so it is neither bookmarked nor shared onward.
  const [landing, setLanding] = useState(() => ({
    serverId: searchParams.get(CONNECT_PARAM) || null,
    binding: searchParams.get(BINDING_PARAM) || null,
  }));
  useEffect(() => {
    if (!searchParams.has(BINDING_PARAM)) return;
    setLanding({
      serverId: searchParams.get(CONNECT_PARAM) || null,
      binding: searchParams.get(BINDING_PARAM) || null,
    });
    const next = new URLSearchParams(searchParams);
    next.delete(BINDING_PARAM);
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);
  const spendBinding = useCallback(
    () => setLanding((current) => ({ ...current, binding: null })),
    [],
  );
  const myConnections = useMyConnections({
    enabled: !mcpOff && upstreamOAuth,
    k2Apps,
  });
  const ownRows = myConnections.data;
  // A capability change must not resurrect a cached K2 account or the empty
  // state of a deep link to an account this deployment now omits.
  const visibleLanding =
    k2Apps ||
    (Array.isArray(ownRows) &&
      ownRows.some((row) => row.server_id === landing.serverId))
      ? landing
      : { serverId: null, binding: null };
  const showMyConnections =
    upstreamOAuth &&
    (myConnections.isLoading ||
      myConnections.isError ||
      Boolean(visibleLanding.serverId) ||
      (Array.isArray(ownRows) && ownRows.length > 0));

  const ownAppStorable = upstreamOAuth && Boolean(ownAppRoutes());
  const apps = useMemo(
    () =>
      connectableApps(data?.apps, { ownAppStorable, upstreamOAuth, k2Apps }),
    [data, ownAppStorable, upstreamOAuth, k2Apps],
  );
  const visible = useMemo(() => filterApps(apps, filters), [apps, filters]);
  const connectedCount = useMemo(() => apps.filter(isConnected).length, [apps]);
  const authOptions = useMemo(() => authOptionsOf(apps), [apps]);
  const openApp = apps.find((app) => app.id === (openId ?? linkedId)) || null;

  const clearFilters = useCallback(
    () => setFilters((current) => ({ ...INITIAL_FILTERS, view: current.view })),
    [],
  );
  const close = useCallback(() => {
    setOpenId(null);
    if (searchParams.has(APP_PARAM)) {
      const next = new URLSearchParams(searchParams);
      next.delete(APP_PARAM);
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);
  const showCategory = useCallback(
    (category) => {
      setFilters((current) => ({ ...current, category }));
      close();
    },
    [close],
  );
  const open = useCallback((app) => setOpenId(app.id), []);

  const filtered =
    filters.search.trim() !== "" ||
    Boolean(filters.category) ||
    Boolean(filters.auth);
  // The filter bar only when there is something to filter: never beside an
  // error or a catalog with no app, where every control would do nothing.
  const showFilters = !isError && (isLoading || apps.length > 0);

  if (mcpOff) {
    return (
      <GatewayPageFrame title="Integrations" testId="integrations-page">
        <MCPOffState description={MCP_OFF_DESCRIPTION} />
      </GatewayPageFrame>
    );
  }

  let body;
  if (isError) {
    body = (
      <GatewayEmptyState
        icon="integrations"
        title="Apps could not be loaded"
        description={`${failureText(error)} Retry, or add an MCP server by its URL.`}
        actionLabel="Retry"
        onAction={() => refetch()}
      />
    );
  } else if (!isLoading && apps.length === 0) {
    body = (
      <GatewayEmptyState
        icon="integrations"
        title="No apps to connect yet"
        description="The catalog lists no app your organization can connect with a key. Add any MCP server by its URL instead."
        actionLabel="Add a custom MCP server"
        onAction={() => navigate(MCP_SERVERS_HREF)}
      />
    );
  } else if (
    !isLoading &&
    visible.length === 0 &&
    filters.view === "connected" &&
    !filtered
  ) {
    body = (
      <GatewayEmptyState
        icon="integrations"
        title="No connected apps yet"
        description="Connect an app from All and it shows here."
        actionLabel="Show all apps"
        onAction={() => setFilters((current) => ({ ...current, view: "all" }))}
      />
    );
  } else if (!isLoading && visible.length === 0) {
    body = (
      <GatewayEmptyState
        variant="no-results"
        title="No apps match"
        description="Try another search, category or auth type."
        actionLabel="Clear filters"
        onAction={clearFilters}
      />
    );
  } else {
    body = (
      <IntegrationsGrid apps={visible} loading={isLoading} onOpen={open} />
    );
  }

  return (
    <GatewayPageFrame
      title="Integrations"
      description="Connect the apps your agents call through the gateway. A key is stored in the vault and never shown again."
      testId="integrations-page"
      actions={
        <Button
          component={RouterLink}
          href={MCP_SERVERS_HREF}
          variant="outlined"
          startIcon={<GatewayIcon name="plus" size={16} />}
        >
          Custom MCP server
        </Button>
      }
    >
      {showMyConnections && (
        <MyConnections
          connections={Array.isArray(ownRows) ? ownRows : []}
          loading={myConnections.isLoading}
          failed={myConnections.isError}
          retrying={myConnections.isFetching}
          onRetry={() => myConnections.refetch()}
          landing={visibleLanding}
          onBindingSpent={spendBinding}
        />
      )}

      {showFilters && (
        <IntegrationsFilterBar
          filters={filters}
          onChange={setFilters}
          connectedCount={connectedCount}
          authOptions={authOptions}
        />
      )}

      {body}

      <AppDetailDrawer
        upstreamOAuth={upstreamOAuth}
        gatewayId={gatewayId}
        app={openApp}
        canManage={canEdit}
        onClose={close}
        onCategory={showCategory}
      />
    </GatewayPageFrame>
  );
}
