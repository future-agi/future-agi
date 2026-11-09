import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Skeleton, Tab, Typography } from "@mui/material";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { paths } from "src/routes/paths";
import { CustomTabs } from "src/components/tabs/tabs";

import {
  GatewayEmptyState,
  GatewayPageFrame,
  GatewayStatusChip,
} from "../../components/frame";
import { ACCESS } from "../catalogRows";
import { MANAGED_MODE_LABEL, OWN_KEY_LABEL } from "../../utils/providerLabel";
import { useCatalogRows } from "../hooks/useCatalogRows";
import { MODEL_TABS, modelIdFromParams, modelTabFrom } from "../modelLinks";
import { orgServedRow } from "../orgServed";
import ModelApi from "./ModelApi";
import ModelOverview, { OrgServedOverview } from "./ModelOverview";
import ModelTryIt from "./ModelTryIt";
import { CopyButton, Mono } from "./Section";

const TAB_LABELS = { overview: "Overview", try: "Try it", api: "API" };
// The way back names the page it opens: the Catalog, or Auto routing on a
// deployment with no managed models, where /models opens Auto routing
// (ModelsSection.jsx).
const CATALOG_BACK = { href: paths.dashboard.gateway.models, label: "Catalog" };
const AUTO_BACK = {
  href: `${paths.dashboard.gateway.models}/auto`,
  label: "Auto routing",
};
// A model the organization's own provider serves, with no catalog row, is
// reached from Providers, where that provider lists it: its way back goes
// there.
const PROVIDERS_BACK = {
  href: paths.dashboard.gateway.providers,
  label: "Providers",
};

function accessChip(row) {
  const managed = row.access === ACCESS.MANAGED || row.access === ACCESS.BOTH;
  if (row.access === ACCESS.NONE)
    return { tone: "neutral", label: "Not callable" };
  if (managed && !row.managedOn && !row.ownRoutes.length) {
    return { tone: "warning", label: "Future AGI credits · off" };
  }
  if (managed && !row.managedOn) {
    return {
      tone: "healthy",
      label: `${OWN_KEY_LABEL} · ${MANAGED_MODE_LABEL} off`,
    };
  }
  return { tone: "healthy", label: row.accessLabel };
}

// For a model only the org's providers serve, when the gateway has none of
// them (the config push left them out): why, from the providers read.
function notPushedNote(row) {
  if (!row.uncatalogued || row.ownRoutes.some((r) => r.pushed)) return "";
  const route = row.ownRoutes[0];
  const reason = route.statusReason.replace(/^not pushed:\s*/i, "");
  return `The gateway does not have ${route.providerName} yet${reason ? `: ${reason}` : ""}. Edit it under Providers.`;
}

function Summary({ row }) {
  const chip = accessChip(row);
  const retire = row.retirement;
  return (
    <Box
      sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1 }}
    >
      <Box
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.25,
          minWidth: 0,
        }}
      >
        <Mono sx={{ color: "text.secondary" }}>{row.id}</Mono>
        <CopyButton
          text={row.id}
          label="Copy model ID"
          message="Model ID copied"
        />
      </Box>
      <Typography typography="s2" color="text.secondary">
        {row.uncatalogued
          ? `Served by ${row.vendorName}`
          : `${row.vendorName} · ${row.typeLabel}`}
      </Typography>
      <GatewayStatusChip tone={chip.tone} label={chip.label} />
      {retire && (
        <GatewayStatusChip
          tone="warning"
          label={retire.date ? `Retires ${retire.date}` : "Deprecated"}
        />
      )}
    </Box>
  );
}

Summary.propTypes = { row: PropTypes.object.isRequired };

export default function ModelDetailPage() {
  const params = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const id = modelIdFromParams(params);
  const tab = modelTabFrom(location.search);
  const data = useCatalogRows();

  const setTab = useCallback(
    (_, next) => {
      const query = new URLSearchParams(location.search);
      if (next === "overview") query.delete("tab");
      else query.set("tab", next);
      const search = query.toString();
      navigate(
        { pathname: location.pathname, search: search ? `?${search}` : "" },
        { replace: true },
      );
    },
    [location.pathname, location.search, navigate],
  );

  const catalogRow = data.rows?.find((r) => r.id === id);
  const providerList = data.providers.data?.providers;
  // Whether the catalog has answered: a deployment with no managed models
  // has none, any other has its rows.
  const catalogSettled =
    Boolean(data.status.data) && (!data.offered || Boolean(data.rows));
  // A model the catalog has no row for still has a page when one of the
  // org's own providers serves it.
  const servedRow = useMemo(
    () =>
      catalogSettled && !catalogRow ? orgServedRow(id, providerList) : null,
    [catalogSettled, catalogRow, id, providerList],
  );
  const row = catalogRow || servedRow;
  const providersPending = !servedRow && data.providers.isLoading;
  let back = data.offered ? CATALOG_BACK : AUTO_BACK;
  if (servedRow) back = PROVIDERS_BACK;

  if (data.catalog.error || (data.status.error && !data.status.data)) {
    return (
      <GatewayPageFrame title={id || "Model"} back={back}>
        <GatewayEmptyState
          icon="catalog"
          title="Couldn't load this model"
          description={
            data.catalog.error?.message ||
            "The gateway did not answer. Try again in a moment."
          }
          actionLabel="Retry"
          onAction={() => {
            data.status.refetch();
            data.catalog.refetch();
          }}
        />
      </GatewayPageFrame>
    );
  }

  // A deployment that offers no managed models has no catalog, so a link to
  // a model no provider of the org serves (futureagi.com/models) says how
  // to call the model instead.
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
      <GatewayPageFrame
        title={<Skeleton width={240} />}
        documentTitle={id || "Model"}
        back={back}
      >
        <Skeleton variant="rounded" height={32} width={420} />
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
          onAction={() => navigate(paths.dashboard.gateway.models)}
        />
      </GatewayPageFrame>
    );
  }

  const note = notPushedNote(row);
  return (
    <GatewayPageFrame
      title={row.name}
      documentTitle={`${row.name} · Models`}
      back={back}
    >
      <Summary row={row} />
      {note && <Alert severity="warning">{note}</Alert>}
      <CustomTabs
        value={tab}
        onChange={setTab}
        aria-label="Model views"
        variant="scrollable"
        scrollButtons={false}
        sx={{ borderBottom: 1, borderColor: "divider", minHeight: 40 }}
      >
        {MODEL_TABS.map((t) => (
          <Tab key={t} value={t} label={TAB_LABELS[t]} sx={{ minHeight: 40 }} />
        ))}
      </CustomTabs>
      {tab === "overview" &&
        (row.uncatalogued ? (
          <OrgServedOverview row={row} />
        ) : (
          <ModelOverview row={row} markupPercent={data.markupPercent} />
        ))}
      {tab === "try" && <ModelTryIt row={row} gatewayId={data.gatewayId} />}
      {tab === "api" && (
        <ModelApi
          gatewayId={data.gatewayId}
          row={row}
          baseUrl={data.baseUrl}
          managedOffered={data.status.data?.available === true}
        />
      )}
    </GatewayPageFrame>
  );
}
