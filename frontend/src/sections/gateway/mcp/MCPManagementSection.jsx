import React, { useCallback } from "react";
import { Box, Tab, Tabs } from "@mui/material";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { paths } from "src/routes/paths";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { useReloadConfig } from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";
import { useMCPStatus } from "./hooks/useMCPConfig";
import MCPStatusBanner from "./MCPStatusBanner";
import MCPOffState from "./MCPOffState";
import { MCP_TABS, MCP_TAB_PARAM, resolveMCPTabIndex } from "./tabs";
import { isMCPTurnedOff } from "./utils";

// The MCP page: header, status banner and tab bar. The open tab lives in the
// query string (`?tab=<slug>`); a link that still carries it in the path
// (`/gateway/mcp/<slug>`) opens the same tab. On a deployment that runs with
// MCP off the page shows that state instead of the banner and tabs.
const MCPManagementSection = () => {
  const { tab: pathSlug } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const tabIndex = resolveMCPTabIndex(
    searchParams.get(MCP_TAB_PARAM) ?? pathSlug,
  );
  const { Content } = MCP_TABS[tabIndex];

  // GatewayGuard renders this page only once the gateway id is known.
  const { gatewayId } = useGatewayContext();
  const { data: mcpStatus, isError: statusError } = useMCPStatus(gatewayId);
  const mcpOff = !statusError && isMCPTurnedOff(mcpStatus);
  const reloadMutation = useReloadConfig();

  const handleTabChange = useCallback(
    (_, index) => {
      const params = new URLSearchParams(searchParams);
      if (index === 0) params.delete(MCP_TAB_PARAM);
      else params.set(MCP_TAB_PARAM, MCP_TABS[index].slug);
      const search = params.toString();
      navigate(
        {
          pathname: paths.dashboard.gateway.mcp,
          search: search ? `?${search}` : "",
        },
        { replace: true },
      );
    },
    [navigate, searchParams],
  );

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

  return (
    <Box p={3}>
      {/* With MCP off there is nothing to manage or reload on this page. */}
      <SectionHeader
        icon={GATEWAY_ICONS.mcp}
        title="MCP Tools"
        subtitle={
          mcpOff
            ? undefined
            : "Manage Model Context Protocol servers, tools, and guardrails"
        }
        actions={
          mcpOff
            ? undefined
            : [
                {
                  label: reloadMutation.isPending
                    ? "Reloading..."
                    : "Reload Config",
                  variant: "outlined",
                  size: "small",
                  icon: "mdi:refresh",
                  onClick: handleReload,
                  disabled: reloadMutation.isPending,
                },
              ]
        }
      />

      {mcpOff ? (
        <MCPOffState />
      ) : (
        <>
          <MCPStatusBanner status={mcpStatus} isError={statusError} />

          <Tabs
            value={tabIndex}
            onChange={handleTabChange}
            variant="scrollable"
            scrollButtons="auto"
            sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}
          >
            {MCP_TABS.map((tab) => (
              <Tab key={tab.slug} label={tab.label} />
            ))}
          </Tabs>

          <Content gatewayId={gatewayId} />
        </>
      )}
    </Box>
  );
};

export default MCPManagementSection;
