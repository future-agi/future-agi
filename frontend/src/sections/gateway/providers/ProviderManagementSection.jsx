import React, { useState, useCallback } from "react";
import { Box, Stack, Tab, Tabs, Card, Skeleton } from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import {
  useGatewayConfig,
  useProviderHealth,
  useReloadConfig,
} from "./hooks/useGatewayConfig";
import { useOrgConfig } from "./hooks/useOrgConfig";
import { useGatewayContext } from "../context/useGatewayContext";
import { useProviderCredentials } from "./hooks/useProviderCredentials";

import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import ProviderHealthTable from "./ProviderHealthTable";
import ProviderConfigView from "./ProviderConfigView";
import RoutingConfigView from "./RoutingConfigView";
import CacheStatusView from "./CacheStatusView";
import AddProviderDialog from "./AddProviderDialog";

const TAB_SLUGS = ["health", "config", "routing", "cache"];

function tabSlugToIndex(slug) {
  const idx = TAB_SLUGS.indexOf(slug);
  return idx >= 0 ? idx : 0;
}

const ProviderManagementSection = () => {
  const { role } = useAuthContext();
  const canWrite =
    RolePermission.OBSERVABILITY[PERMISSIONS.CREATE_EDIT_PROJECT][role];
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const tab = tabSlugToIndex(tabSlug);

  const handleTabChange = useCallback(
    (_, newIndex) => {
      if (newIndex === 0) {
        navigate("/dashboard/gateway/providers", { replace: true });
      } else {
        navigate(`/dashboard/gateway/providers/${TAB_SLUGS[newIndex]}`, {
          replace: true,
        });
      }
    },
    [navigate],
  );
  const [addProviderOpen, setAddProviderOpen] = useState(false);

  const { gatewayId, isLoading: gwLoading } = useGatewayContext();

  const { data: config, isLoading: configLoading } =
    useGatewayConfig(gatewayId);
  const { data: orgConfig, isLoading: orgConfigLoading } = useOrgConfig();
  const { data: providerHealth, isLoading: healthLoading } =
    useProviderHealth(gatewayId);
  const { data: providerCredentials, isLoading: credentialsLoading } =
    useProviderCredentials();
  const reloadMutation = useReloadConfig();

  const isLoading =
    gwLoading || configLoading || healthLoading || orgConfigLoading || credentialsLoading;

  const handleReload = () => {
    if (!gatewayId) return;
    reloadMutation.mutate(gatewayId, {
      onSuccess: () => {
        enqueueSnackbar("Configuration reloaded", { variant: "success" });
      },
      onError: () => {
        enqueueSnackbar("Failed to reload config", { variant: "error" });
      },
    });
  };

  if (isLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={220} height={40} />
          <Stack direction="row" spacing={1}>
            <Skeleton width={120} height={36} variant="rounded" />
            <Skeleton width={120} height={36} variant="rounded" />
          </Stack>
        </Stack>
        <Skeleton width="50%" height={40} sx={{ mb: 3 }} />
        <Card>
          {[...Array(4)].map((_, i) => (
            <Stack
              key={i}
              direction="row"
              spacing={2}
              sx={{
                px: 2,
                py: 1.5,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Skeleton width="20%" height={20} />
              <Skeleton width="15%" height={20} />
              <Skeleton width="25%" height={20} />
              <Skeleton width="15%" height={20} />
              <Skeleton width="15%" height={20} />
            </Stack>
          ))}
        </Card>
      </Box>
    );
  }

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.providers}
        title="Providers"
        subtitle="Manage LLM providers, routing rules, and cache settings"
        actions={[
          ...(canWrite
            ? [
                {
                  label: "Add Provider",
                  variant: "contained",
                  size: "small",
                  icon: "mdi:plus",
                  onClick: () => setAddProviderOpen(true),
                },
              ]
            : []),
          {
            label: reloadMutation.isPending ? "Reloading..." : "Reload Config",
            variant: "outlined",
            size: "small",
            icon: "mdi:refresh",
            onClick: handleReload,
            disabled: reloadMutation.isPending,
          },
        ]}
      />

      <Tabs
        value={tab}
        onChange={handleTabChange}
        sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}
      >
        <Tab label="Provider Health" />
        <Tab label="Provider Config" />
        <Tab label="Routing" />
        <Tab label="Cache" />
      </Tabs>

      {tab === 0 && (
        <ProviderHealthTable
          providerHealth={providerHealth}
          orgConfig={orgConfig}
          providerCredentials={providerCredentials}
        />
      )}
      {tab === 1 && (
        <ProviderConfigView
          config={config}
          orgConfig={orgConfig}
          gatewayId={gatewayId}
          providerCredentials={providerCredentials}
        />
      )}
      {tab === 2 && <RoutingConfigView config={config} gatewayId={gatewayId} />}
      {tab === 3 && <CacheStatusView config={config} gatewayId={gatewayId} />}

      <AddProviderDialog
        open={addProviderOpen}
        onClose={() => setAddProviderOpen(false)}
        gatewayId={gatewayId}
      />
    </Box>
  );
};

export default ProviderManagementSection;
