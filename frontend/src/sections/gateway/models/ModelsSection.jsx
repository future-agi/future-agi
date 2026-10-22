import React, { useCallback } from "react";
import { Box, Skeleton, Tab, Tabs } from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";
import { paths } from "src/routes/paths";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { useGatewayContext } from "../context/useGatewayContext";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import AutoRoutingTab from "./AutoRoutingTab";
import ManagedModelsCard from "./ManagedModelsCard";
import ModelCatalog from "./ModelCatalog";
import {
  useManagedCatalog,
  useManagedModelsStatus,
} from "./hooks/useManagedModels";

// The tabs, in order; the first opens at the bare Models URL. The catalog
// lists managed models, so it is a tab only where the deployment offers them.
const CATALOG_TABS = ["catalog", "auto"];
const AUTO_ONLY_TABS = ["auto"];
const TAB_LABELS = { catalog: "Catalog", auto: "Auto routing" };

const ModelsSection = () => {
  // Turning managed models on or off and saving auto routing write the org's
  // gateway config: Owner or Admin only (useGatewayAdmin, the backend's
  // is_gateway_admin).
  const { canEdit: canWrite } = useGatewayAdmin();
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();

  const { gateway, gatewayId } = useGatewayContext();
  const status = useManagedModelsStatus();
  // Only a deployment that says it offers managed models has a catalog to
  // read. Until the status says so (and when it cannot be read), the page
  // offers Auto routing, which works with the org's own providers.
  const catalogOffered = status.data?.available === true;
  const catalog = useManagedCatalog({ enabled: catalogOffered });

  const tabSlugs = catalogOffered ? CATALOG_TABS : AUTO_ONLY_TABS;
  const tab = Math.max(tabSlugs.indexOf(tabSlug), 0);
  const handleTabChange = useCallback(
    (_, index) => {
      const root = paths.dashboard.gateway.models;
      navigate(index === 0 ? root : `${root}/${tabSlugs[index]}`, {
        replace: true,
      });
    },
    [navigate, tabSlugs],
  );

  // No subtitle while the status loads or when it fails: either one could
  // be wrong.
  let subtitle;
  if (catalogOffered) {
    subtitle =
      'Browse the models the gateway can call, and tune how model "auto" chooses between them';
  } else if (status.data?.available === false) {
    subtitle =
      'Tune how model "auto" chooses between the models your providers serve';
  }

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.models}
        title="Models"
        subtitle={subtitle}
      />

      <ManagedModelsCard
        status={status.data}
        isLoading={status.isLoading}
        error={status.error}
        onRetry={status.refetch}
        gatewayId={gatewayId}
        canWrite={Boolean(canWrite)}
      />

      {status.isLoading ? (
        <Skeleton variant="rounded" height={340} />
      ) : (
        <>
          <Tabs
            value={tab}
            onChange={handleTabChange}
            sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}
          >
            {tabSlugs.map((slug) => (
              <Tab key={slug} label={TAB_LABELS[slug]} />
            ))}
          </Tabs>

          {tabSlugs[tab] === "catalog" && (
            <ModelCatalog
              catalog={catalog.data}
              isLoading={catalog.isLoading}
              error={catalog.error}
              onRetry={catalog.refetch}
              managedStatus={status.data}
              baseUrl={gateway?.baseUrl}
            />
          )}
          {tabSlugs[tab] === "auto" && (
            <AutoRoutingTab
              gatewayId={gatewayId}
              canWrite={Boolean(canWrite)}
              catalogModels={catalog.data?.models}
              managedStatus={status.data}
              baseUrl={gateway?.baseUrl}
            />
          )}
        </>
      )}
    </Box>
  );
};

export default ModelsSection;
