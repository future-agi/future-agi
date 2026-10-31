import React, { useState, useCallback } from "react";
import { Box, Stack, Tab, Tabs, Card, Skeleton, Alert } from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import {
  useGatewayConfig,
  useReloadConfig,
} from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";
import {
  useMCPStatus,
  useMCPTools,
  useMCPResources,
  useMCPPrompts,
} from "./hooks/useMCPConfig";

import MCPOverviewTab from "./MCPOverviewTab";
import MCPToolsTab from "./MCPToolsTab";
import MCPServersTab from "./MCPServersTab";
import MCPGuardrailsTab from "./MCPGuardrailsTab";
import MCPPlaygroundTab from "./MCPPlaygroundTab";
import MCPResourcesTab from "./MCPResourcesTab";
import MCPPromptsTab from "./MCPPromptsTab";
import AddMCPServerDialog from "./AddMCPServerDialog";

const TAB_SLUGS = [
  "overview",
  "tools",
  "servers",
  "resources",
  "prompts",
  "guardrails",
  "playground",
];

function tabSlugToIndex(slug) {
  const idx = TAB_SLUGS.indexOf(slug);
  return idx >= 0 ? idx : 0;
}

const MCPManagementSection = () => {
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const tab = tabSlugToIndex(tabSlug);

  const handleTabChange = useCallback(
    (_, newIndex) => {
      if (newIndex === 0) {
        navigate("/dashboard/gateway/mcp", { replace: true });
      } else {
        navigate(`/dashboard/gateway/mcp/${TAB_SLUGS[newIndex]}`, {
          replace: true,
        });
      }
    },
    [navigate],
  );

  const [addServerOpen, setAddServerOpen] = useState(false);
  const [editServer, setEditServer] = useState(null);

  const { gatewayId, isLoading: gwLoading } = useGatewayContext();

  const { data: config, isLoading: configLoading } =
    useGatewayConfig(gatewayId);
  const { data: mcpStatus, isLoading: statusLoading } = useMCPStatus(gatewayId);
  const { data: mcpTools, isLoading: toolsLoading } = useMCPTools(gatewayId);
  const { data: mcpResources, isLoading: resourcesLoading } =
    useMCPResources(gatewayId);
  const { data: mcpPrompts, isLoading: promptsLoading } =
    useMCPPrompts(gatewayId);
  const reloadMutation = useReloadConfig();

  const isLoading = gwLoading || configLoading || statusLoading;

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

  const handleEditServer = (serverId, serverConfig) => {
    setEditServer({ serverId, config: serverConfig });
    setAddServerOpen(true);
  };

  const handleCloseDialog = () => {
    setAddServerOpen(false);
    setEditServer(null);
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
            </Stack>
          ))}
        </Card>
      </Box>
    );
  }

  const mcpConfig = config?.mcp || {};
  const mcpEnabled = mcpConfig.enabled !== false;

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.mcp}
        title="MCP Tools"
        subtitle="Manage Model Context Protocol servers, tools, and guardrails"
        actions={[
          {
            label: "Add Server",
            variant: "contained",
            size: "small",
            icon: "mdi:plus",
            onClick: () => setAddServerOpen(true),
          },
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

      {!mcpEnabled && (
        <Alert severity="info" sx={{ mb: 3 }}>
          MCP is not enabled in the gateway configuration. Add an MCP server to
          get started, or enable the &quot;mcp.enabled&quot; flag in the gateway
          config.
        </Alert>
      )}

      <Tabs
        value={tab}
        onChange={handleTabChange}
        sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}
      >
        <Tab label="Overview" />
        <Tab label="Tools" />
        <Tab label="Servers" />
        <Tab label="Resources" />
        <Tab label="Prompts" />
        <Tab label="Guardrails" />
        <Tab label="Playground" />
      </Tabs>

      {tab === 0 && <MCPOverviewTab mcpStatus={mcpStatus} />}
      {tab === 1 && (
        <MCPToolsTab mcpTools={mcpTools} isLoading={toolsLoading} />
      )}
      {tab === 2 && (
        <MCPServersTab
          config={config}
          mcpStatus={mcpStatus}
          gatewayId={gatewayId}
          onEditServer={handleEditServer}
        />
      )}
      {tab === 3 && (
        <MCPResourcesTab
          mcpResources={mcpResources}
          isLoading={resourcesLoading}
        />
      )}
      {tab === 4 && (
        <MCPPromptsTab mcpPrompts={mcpPrompts} isLoading={promptsLoading} />
      )}
      {/* Keep the draft mounted while navigating between MCP tabs. */}
      <Box hidden={tab !== 5}>
        <MCPGuardrailsTab
          key={gatewayId}
          config={config}
          mcpStatus={mcpStatus}
          gatewayId={gatewayId}
        />
      </Box>
      {tab === 6 && (
        <MCPPlaygroundTab mcpTools={mcpTools} gatewayId={gatewayId} />
      )}

      <AddMCPServerDialog
        open={addServerOpen}
        onClose={handleCloseDialog}
        gatewayId={gatewayId}
        editServer={editServer}
      />
    </Box>
  );
};

export default MCPManagementSection;
