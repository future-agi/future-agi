import React, { useCallback, useMemo, useState } from "react";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Tab from "@mui/material/Tab";
import Typography from "@mui/material/Typography";
import { useNavigate, useParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";

import { GatewayEmptyState, GatewayPageFrame } from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayContext } from "../context/useGatewayContext";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import AddProviderDialog from "./AddProviderDialog";
import CacheStatusView from "./CacheStatusView";
import ProviderList from "./ProviderList";
import RoutingConfigView from "./RoutingConfigView";
import {
  useGatewayConfig,
  useProviderHealth,
  useReloadConfig,
} from "./hooks/useGatewayConfig";
import { useOrgConfig } from "./hooks/useOrgConfig";
import { buildProviderRows } from "./provider-rows";
import CreditBalanceChip from "./ui/CreditBalanceChip";
import {
  CACHE_OFF_LINE,
  providerTabIndex,
  providerTabsFor,
} from "./providerTabs";
import { sectionEnforcement } from "../settings/sectionEnforcement";
import FaIcon from "./ui/FaIcon";

// The API's error text (Django answers { result: "..." }) or axios's.
function readError(error) {
  if (typeof error?.result === "string") return error.result;
  return error?.message || "";
}

// Providers & keys > Providers: the org's own provider keys (BYOK) and how
// the gateway reaches each one, on the gateway page frame. Adding, editing
// and removing a provider, the routing and cache writes, and Reload config
// (as on Gateway settings) act on the org's gateway config: Owner or Admin
// only (useGatewayAdmin, the backend's is_gateway_admin). Everyone sees the
// controls; for a Member or a Viewer they are disabled and say why. Every
// read stays open.
export default function ProviderManagementSection() {
  const { canEdit } = useGatewayAdmin();
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const [addOpen, setAddOpen] = useState(false);

  const {
    gatewayId,
    isLoading: gatewayLoading,
    managedOffered,
  } = useGatewayContext();
  // The page shows its own error state for this read, so no app-wide toast.
  const configQuery = useGatewayConfig(gatewayId, {
    meta: { errorHandled: true },
  });
  const orgConfigQuery = useOrgConfig();
  const healthQuery = useProviderHealth(gatewayId);
  const reload = useReloadConfig();

  const config = configQuery.data;
  // Cache stays visible; the off-state panel shows one notice.
  const cacheOff = sectionEnforcement(configQuery, "cache") === "not_enforced";
  const TABS = providerTabsFor();
  const tabSlugShown = TABS[providerTabIndex(TABS, tabSlug)].slug;
  const tab = providerTabIndex(TABS, tabSlug);
  const rows = useMemo(
    () =>
      config || orgConfigQuery.data
        ? buildProviderRows(config, orgConfigQuery.data, healthQuery.data)
        : undefined,
    [config, orgConfigQuery.data, healthQuery.data],
  );
  const loading =
    gatewayLoading || configQuery.isLoading || orgConfigQuery.isLoading;
  const failed = configQuery.isError && !orgConfigQuery.data;

  // Routing and Cache are forms over the gateway's stored config. Until it
  // is read they show nothing to edit: a default must never pass for a
  // stored value, and an edit made early must not be wiped when the read
  // lands. A failed read says so, with Retry.
  const configForm = (form) => {
    if (config) return form;
    if (configQuery.isError) {
      const reason = readError(configQuery.error);
      return (
        <GatewayEmptyState
          icon="providers"
          title="The gateway config could not be loaded"
          description={`The gateway's config did not load${
            reason ? `: ${reason}` : ""
          }. Check that the gateway is reachable, then retry.`}
          actionLabel="Retry"
          onAction={() => configQuery.refetch()}
        />
      );
    }
    return (
      <Skeleton
        variant="rounded"
        height={320}
        data-testid="provider-config-loading"
      />
    );
  };

  const handleTabChange = useCallback(
    (_, index) => {
      const slug = TABS[index].slug;
      navigate(
        slug
          ? `${paths.dashboard.gateway.providers}/${slug}`
          : paths.dashboard.gateway.providers,
        { replace: true },
      );
    },
    [navigate, TABS],
  );

  const handleReload = () => {
    if (!canEdit || !gatewayId) return;
    // A reload the gateway did not apply is reported once, as a warning, by
    // useGatewayConfigWrite; neither toast here runs for it.
    reload.mutate(gatewayId, {
      onSuccess: () => {
        enqueueSnackbar("Configuration reloaded", { variant: "success" });
      },
      onError: () => {
        enqueueSnackbar("Failed to reload config", { variant: "error" });
      },
    });
  };

  return (
    <GatewayPageFrame
      title="Providers"
      description="Your own provider keys, and how the gateway reaches each provider."
      testId="gateway-providers-page"
      actions={
        <>
          <CreditBalanceChip />
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              variant="outlined"
              startIcon={<FaIcon name="reload" />}
              onClick={handleReload}
              disabled={!canEdit || !gatewayId || reload.isPending}
            >
              {reload.isPending ? "Reloading..." : "Reload config"}
            </Button>
          </GatewayAdminOnly>
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              variant="contained"
              color="primary"
              startIcon={<FaIcon name="add" />}
              onClick={() => setAddOpen(true)}
              disabled={!canEdit}
            >
              Add provider
            </Button>
          </GatewayAdminOnly>
        </>
      }
    >
      <CustomTabs
        value={tab}
        onChange={handleTabChange}
        variant="scrollable"
        allowScrollButtonsMobile
        sx={{ minHeight: 36, borderBottom: 1, borderColor: "divider" }}
      >
        {TABS.map((t) => (
          <Tab
            key={t.label}
            label={t.label}
            sx={{ minHeight: 36, textTransform: "none" }}
          />
        ))}
      </CustomTabs>

      {cacheOff && tabSlugShown !== "cache" && (
        <Typography typography="s2" color="text.secondary">
          {CACHE_OFF_LINE}
        </Typography>
      )}

      {tab === 0 &&
        (failed ? (
          <GatewayEmptyState
            icon="providers"
            title="Providers could not be loaded"
            description={`The gateway's provider list did not load${
              readError(configQuery.error)
                ? `: ${readError(configQuery.error)}`
                : ""
            }. Check that the gateway is reachable, then retry.`}
            actionLabel="Retry"
            onAction={() => configQuery.refetch()}
          />
        ) : (
          <ProviderList
            rows={rows}
            loading={loading}
            gatewayId={gatewayId}
            canWrite={canEdit}
            onAdd={() => setAddOpen(true)}
            managedOffered={managedOffered}
            orgProviders={healthQuery.data?.providers}
          />
        ))}
      {tabSlugShown === "routing" &&
        configForm(<RoutingConfigView config={config} gatewayId={gatewayId} />)}
      {tabSlugShown === "cache" &&
        configForm(<CacheStatusView config={config} gatewayId={gatewayId} />)}

      <AddProviderDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        gatewayId={gatewayId}
        existingNames={Object.keys({
          ...orgConfigQuery.data?.providers,
          ...config?.providers,
        })}
      />
    </GatewayPageFrame>
  );
}
