import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Skeleton, Tab, Typography } from "@mui/material";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { paths } from "src/routes/paths";
import { CustomTabs } from "src/components/tabs/tabs";

import {
  GatewayEmptyState,
  GatewayPageFrame,
  GatewayStatusChip,
} from "../../components/frame";
import { ACCESS } from "../catalogRows";
import { catalogBrowseState } from "../catalog/queryState";
import { MANAGED_MODE_LABEL, OWN_KEY_LABEL } from "../../utils/providerLabel";
import { useCatalogRows } from "../hooks/useCatalogRows";
import { MODEL_TABS, modelIdFromParams, modelTabFrom } from "../modelLinks";
import { orgServedRow } from "../orgServed";
import ModelApi from "./ModelApi";
import ModelOverview, { OrgServedOverview } from "./ModelOverview";
import ModelTryIt, { createTryState } from "./ModelTryIt";
import { CopyButton, Mono } from "./Section";

const TAB_LABELS = { overview: "Overview", try: "Try it", api: "API" };

function accessChip(row, unverified) {
  const managed = row.access === ACCESS.MANAGED || row.access === ACCESS.BOTH;
  if (row.access === ACCESS.NONE) {
    return { tone: "neutral", label: unverified ? "Provider access not verified" : "Not callable" };
  }
  if (managed && !row.managedOn && !row.ownRoutes.some((r) => r.pushed !== false)) {
    return { tone: "warning", label: "Future AGI credits · off" };
  }
  if (managed && !row.managedOn) {
    return { tone: "healthy", label: `${OWN_KEY_LABEL} · ${MANAGED_MODE_LABEL} off` };
  }
  return { tone: "healthy", label: row.accessLabel };
}

function notPushedNote(row) {
  const route = row.ownRoutes.find((r) => r.pushed === false);
  if (!route) return "";
  const reason = (route.statusReason || "").replace(/^not pushed:\s*/i, "");
  return `The gateway does not have ${route.providerName} yet${reason ? `: ${reason}` : ""}. Edit it under Providers.`;
}

function Summary({ row, unverified }) {
  const chip = accessChip(row, unverified);
  const retire = row.retirement;
  return (
    <Box sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1 }}>
      <Box sx={{ display: "inline-flex", alignItems: "center", gap: 0.25, minWidth: 0 }}>
        <Mono sx={{ color: "text.secondary" }}>{row.id}</Mono>
        <CopyButton text={row.id} label="Copy model ID" message="Model ID copied" />
      </Box>
      <Typography typography="s2" color="text.secondary">
        {row.uncatalogued
          ? `Served by ${row.vendorName} · Type not reported`
          : `${row.vendorName} · ${row.typeLabel}`}
      </Typography>
      <GatewayStatusChip tone={chip.tone} label={chip.label} />
      {retire && (
        <GatewayStatusChip tone="warning" label={retire.date ? `Retires ${retire.date}` : "Deprecated"} />
      )}
    </Box>
  );
}
Summary.propTypes = { row: PropTypes.object.isRequired, unverified: PropTypes.bool };

// The keyed boundary resets in-memory work on model/gateway changes, not tabs.
// Tab continuity keeps drafts, results and snapshots in memory, not view-state storage.
export default function ModelDetailPage() {
  const params = useParams();
  const id = modelIdFromParams(params);
  const data = useCatalogRows();
  return <ModelDetailContent key={`${data.gatewayId}:${id}`} id={id} data={data} />;
}

function ModelDetailContent({ id, data }) {
  const location = useLocation();
  const navigate = useNavigate();
  const tab = modelTabFrom(location.search);
  const [tryState, setTryState] = useState(createTryState);
  const [requestSnapshot, setRequestSnapshot] = useState(null);
  const browse = catalogBrowseState(location.state?.gatewayModelBrowse);
  const back = location.state?.gatewayModelOrigin === "your"
    ? { href: `${paths.dashboard.gateway.models}/your`, label: "Your models" }
    : { href: `${browse.pathname}${browse.search}`, label: "Models" };
  const setTab = useCallback(
    (_, next) => {
      const query = new URLSearchParams(location.search);
      if (next === "overview") query.delete("tab");
      else query.set("tab", next);
      const search = query.toString();
      navigate(
        { pathname: location.pathname, search: search ? `?${search}` : "" },
        { replace: true, state: location.state },
      );
    },
    [location.pathname, location.search, location.state, navigate],
  );

  const catalogRow = data.rows?.find((r) => r.id === id);
  const providerList = data.providers.data?.providers;
  const catalogSettled = Boolean(data.status.data) && (!data.offered || Boolean(data.rows));
  const servedRow = useMemo(
    () => catalogSettled && !catalogRow ? orgServedRow(id, providerList) : null,
    [catalogSettled, catalogRow, id, providerList],
  );
  const row = catalogRow || servedRow;
  const providersPending = !Array.isArray(providerList) && data.providers.isLoading;
  const unverified = Boolean(data.providers.error) && !Array.isArray(providerList);
  const retryModel = () => { data.status.refetch(); data.catalog.refetch(); };

  if ((data.catalog.error && !data.catalog.data) || (data.status.error && !data.status.data)) {
    return (
      <GatewayPageFrame title={id || "Model"} back={back}>
        <GatewayEmptyState
          icon="catalog"
          title="Couldn't load this model"
          description={data.catalog.error?.message || "The gateway did not answer. Try again in a moment."}
          actionLabel="Retry"
          onAction={retryModel}
        />
      </GatewayPageFrame>
    );
  }

  // A failed provider read is not evidence that a provider/model is absent.
  if (!row && data.providers.error) {
    return (
      <GatewayPageFrame title={id || "Model"} back={back}>
        <GatewayEmptyState
          icon="catalog"
          title="Could not verify your providers"
          description="Your provider list could not be loaded. Retry to check this model's routes."
          actionLabel="Retry"
          onAction={() => data.providers.refetch()}
        />
      </GatewayPageFrame>
    );
  }

  if (data.status.data && !data.offered && !servedRow && !providersPending) {
    return (
      <GatewayPageFrame title={id || "Model"} back={back}>
        <GatewayEmptyState
          icon="catalog"
          title="Call it with your own provider key"
          description={`No provider of your organization serves ${id || "this model"}. Add a provider that serves it under Providers, then send its model id with one of your API keys.`}
          actionLabel="Go to Providers"
          onAction={() => navigate(paths.dashboard.gateway.providers)}
        />
      </GatewayPageFrame>
    );
  }

  if (!row && (!data.rows || providersPending)) {
    return (
      <GatewayPageFrame title={<Skeleton width={240} />} documentTitle={id || "Model"} back={back}>
        <Skeleton variant="rounded" height={32} width={420} sx={{ maxWidth: "100%" }} />
        <Skeleton variant="rounded" height={88} />
        <Skeleton variant="rounded" height={260} />
      </GatewayPageFrame>
    );
  }

  if (!row) {
    return (
      <GatewayPageFrame title={id || "Model"} back={back}>
        <GatewayEmptyState
          icon="catalog"
          title="This model is not in the catalog"
          description="This gateway's catalog does not list it. It may have been retired, or the link may be mistyped."
          actionLabel="Back to the catalog"
          onAction={() => navigate(back.href, { state: location.state })}
        />
      </GatewayPageFrame>
    );
  }

  const note = notPushedNote(row);
  const routesUnknown = (unverified || providersPending) && row.access === ACCESS.NONE;
  return (
    <GatewayPageFrame title={row.name} documentTitle={`${row.name} · Models`} back={back}>
      <Summary row={row} unverified={unverified || providersPending} />
      {data.providers.error && (
        <Alert severity="warning" action={<Button color="inherit" size="small" onClick={() => data.providers.refetch()}>Retry</Button>}>
          <Typography typography="s2" fontWeight={600}>Could not verify your providers</Typography>
          {unverified ? "Retry to check this model's routes." : "Showing the last loaded provider routes. Retry to check for changes."}
        </Alert>
      )}
      {(data.catalog.error || data.status.error) && (
        <Alert severity="warning" action={<Button color="inherit" size="small" onClick={retryModel}>Retry model</Button>}>
          Could not refresh this model. Showing the last loaded information.
        </Alert>
      )}
      {note && <Alert severity="warning">{note}</Alert>}
      <CustomTabs
        value={tab}
        onChange={setTab}
        aria-label="Model views"
        variant="scrollable"
        scrollButtons={false}
        sx={{ borderBottom: 1, borderColor: "divider", minHeight: 40 }}
      >
        {MODEL_TABS.map((t) => <Tab key={t} value={t} label={TAB_LABELS[t]} sx={{ minHeight: 40 }} />)}
      </CustomTabs>
      {routesUnknown && providersPending && <Skeleton variant="rounded" height={88} />}
      {!routesUnknown && tab === "overview" && (row.uncatalogued
        ? <OrgServedOverview row={row} />
        : <ModelOverview row={row} markupPercent={data.markupPercent} />)}
      {!routesUnknown && tab === "try" && (
        <ModelTryIt
          row={row}
          gatewayId={data.gatewayId}
          state={tryState}
          onStateChange={setTryState}
          onOpenApi={() => setTab(null, "api")}
          onUseRequest={(snapshot) => { setRequestSnapshot(snapshot); setTab(null, "api"); }}
          delegatedPlaygroundAvailable={data.gateway?.delegatedPlaygroundAvailable}
        />
      )}
      {!routesUnknown && tab === "api" && requestSnapshot && row.uncatalogued && (
        <Alert severity="info">Your submitted request is a chat request. Choose Chat in Request kind to view its code. This does not send another request or verify other model capabilities.</Alert>
      )}
      {!routesUnknown && tab === "api" && (
        <ModelApi
          gatewayId={data.gatewayId}
          row={row}
          baseUrl={data.baseUrl}
          hosted={data.hosted}
          deploymentModeConfirmed={data.deploymentModeConfirmed}
          retryDeploymentMode={data.retryDeploymentMode}
          managedOffered={data.status.data?.available === true}
          requestSnapshot={requestSnapshot}
        />
      )}
    </GatewayPageFrame>
  );
}
ModelDetailContent.propTypes = { id: PropTypes.string.isRequired, data: PropTypes.object.isRequired };
