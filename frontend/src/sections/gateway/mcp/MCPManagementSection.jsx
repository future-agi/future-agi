import React, { useCallback, useEffect, useId, useMemo, useState } from "react";
import Typography from "@mui/material/Typography";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Tab from "@mui/material/Tab";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import { paths } from "src/routes/paths";
import { CustomTabs } from "src/components/tabs/tabs";

import { GatewayPageFrame, GatewayStatusChip } from "../components/frame";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import {
  useGatewayConfig,
  SavedButNotAppliedError,
  useReloadConfig,
} from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";
import { apiErrorMessage } from "./api";
import { useMCPStatus } from "./hooks/useMCPConfig";
import { useMCPConfigPatch } from "./hooks/useMCPConfigPatch";
import McpIcon from "./components/McpIcon";
import MCPStatusBanner, { mcpVerdict } from "./MCPStatusBanner";
import {
  DISABLED_MCP_NOTICE,
  MCP_TAB_PARAM,
  isVisibleMCPTab,
  resolveMCPTabIndex,
  shownMCPTabs,
} from "./tabs";
import { isMCPRuntimeOff } from "./hooks/mcpRuntimeState";
import { toolCallFeatures } from "./hooks/useMCPToolCallFeatures";
import SiemExportDrawer from "./calls/SiemExportDrawer";

// The MCP page: the FA page frame, the MCP verdict (a chip, and a banner when
// something needs attention), the tab bar and the open tab. The open tab
// lives in the query string (`?tab=<slug>`); a link that still carries it in
// the path (`/gateway/mcp/<slug>`) opens the same tab. Deployment-off uses
// the baseline allowlist, with saved edits and no NEW surfaces. Personal
// client grants remain independently revocable when client OAuth is enabled.
// The bar's tabs follow deployment capabilities
// (tabs.js shownMCPTabs).

const MCPManagementSection = () => {
  const { tab: pathSlug } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  // GatewayGuard renders this page only once the gateway id is known.
  const { gatewayId } = useGatewayContext();
  const { data: config } = useGatewayConfig(gatewayId);
  const {
    data: mcpStatus,
    isError: statusError,
    isLoading: statusLoading,
  } = useMCPStatus(gatewayId);
  // Wait only for the first answer or bounded failure. Background status reads
  // must not unmount saved controls; late observers do not retry errors on mount.
  const [settledGateway, setSettledGateway] = useState(null);
  useEffect(() => {
    if (!statusLoading || statusError) setSettledGateway(gatewayId);
  }, [gatewayId, statusLoading, statusError]);
  const waitingForStatus = statusLoading && settledGateway !== gatewayId;
  const clientOAuth =
    !statusError && mcpStatus?.features?.client_oauth === true;
  const clientInstalls =
    !statusError && mcpStatus?.features?.client_installs === true;
  // Share the cache's last authoritative restriction with live-list guards;
  // the current verdict still reports rejected or semantically failed reads.
  const mcpOff = isMCPRuntimeOff(mcpStatus);
  const { explorer: toolCallExplorer } = toolCallFeatures({
    data: mcpStatus,
    isError: statusError,
    isLoading: statusLoading,
  });
  const tabs = useMemo(
    () =>
      shownMCPTabs({
        clientOAuth,
        clientInstalls,
        toolCallExplorer,
        runtimeOff: mcpOff,
      }),
    [clientOAuth, clientInstalls, toolCallExplorer, mcpOff],
  );
  const requestedSlug = searchParams.get(MCP_TAB_PARAM) ?? pathSlug;
  const tabIndex = resolveMCPTabIndex(requestedSlug, tabs);
  const { Content, disabled } = tabs[tabIndex];
  const disabledNoticeId = useId();
  // SIEM renders only when MCP is on AND the SIEM switch is on,
  // independently of the tool-call explorer and download capabilities.
  const siemAvailable =
    !statusError &&
    !statusLoading &&
    !mcpOff &&
    mcpStatus?.enabled === true &&
    // Match reportsRuntimeAvailable in mcpRuntimeState: not-known-OFF alone
    // is not authoritative ON. Do not change the saved-control shell verdict.
    (mcpStatus.reason == null ||
      mcpStatus.reason === "gateway_not_org_scoped") &&
    mcpStatus?.features?.siem_export === true;
  const [siemOpen, setSiemOpen] = useState(false);
  useEffect(() => {
    if (!siemAvailable) setSiemOpen(false);
  }, [siemAvailable]);

  // A link to a tab that is not shown opens the overview, and the URL says
  // so.
  useEffect(() => {
    // Keep a pending capability-gated deep link until status resolves, but do
    // not mount its component before the capability is explicitly enabled.
    if (statusLoading && !statusError) return;
    if (!requestedSlug || isVisibleMCPTab(requestedSlug, tabs)) return;
    const params = new URLSearchParams(searchParams);
    params.delete(MCP_TAB_PARAM);
    const search = params.toString();
    navigate(
      {
        pathname: paths.dashboard.gateway.mcp,
        search: search ? `?${search}` : "",
      },
      { replace: true },
    );
  }, [navigate, requestedSlug, searchParams, tabs, statusLoading, statusError]);

  // Turning MCP on writes the org's gateway config: Owner or Admin only.
  const { canEdit } = useGatewayAdmin();
  const reloadMutation = useReloadConfig();
  const patch = useMCPConfigPatch(gatewayId);
  const mcpConfig = config ? config.mcp || {} : undefined;
  const verdict = mcpVerdict({
    status: mcpStatus,
    isError: statusError,
    mcpConfig,
  });

  const handleTabChange = useCallback(
    (_, index) => {
      if (tabs[index].disabled) return;
      const params = new URLSearchParams(searchParams);
      if (index === 0) params.delete(MCP_TAB_PARAM);
      else params.set(MCP_TAB_PARAM, tabs[index].slug);
      const search = params.toString();
      navigate(
        {
          pathname: paths.dashboard.gateway.mcp,
          search: search ? `?${search}` : "",
        },
        { replace: true },
      );
    },
    [navigate, searchParams, tabs],
  );

  const handleReload = () => {
    if (!gatewayId || !canEdit || reloadMutation.isPending) return;
    reloadMutation.mutate(gatewayId, {
      onSuccess: () => {
        enqueueSnackbar("Configuration reloaded", { variant: "success" });
      },
      onError: () => {
        enqueueSnackbar("Failed to reload config", { variant: "error" });
      },
    });
  };

  const turnOn = () => {
    if (!canEdit || mcpOff || patch.isPending || !gatewayId) return;
    patch.mutate(
      { enabled: true },
      {
        onSuccess: (result) => {
          if (result?.gateway_synced === false) {
            enqueueSnackbar(new SavedButNotAppliedError(result).message, {
              variant: "warning",
            });
          } else {
            enqueueSnackbar("MCP is on for this organization", {
              variant: "success",
            });
          }
        },
        onError: (err) =>
          enqueueSnackbar(apiErrorMessage(err, "MCP could not be turned on."), {
            variant: "error",
          }),
      },
    );
  };

  return (
    <GatewayPageFrame
      title="MCP tools"
      description={
        mcpOff ? undefined : "Manage MCP servers and saved settings."
      }
      // W14 SIEM action/drawer requires MCP ON AND SIEM ON, never retained OFF.
      // Explorer/download gates are independent and do not hide base MCP controls.
      actions={
        <>
          {verdict.label && (
            <GatewayStatusChip tone={verdict.tone} label={verdict.label} />
          )}
          {siemAvailable && (
            <Button variant="outlined" onClick={() => setSiemOpen(true)}>
              SIEM export
            </Button>
          )}
          <Button
            variant="outlined"
            startIcon={<McpIcon name="refresh" size={16} />}
            onClick={handleReload}
            disabled={!canEdit || reloadMutation.isPending}
          >
            {reloadMutation.isPending ? "Reloading" : "Reload config"}
          </Button>
        </>
      }
    >
      <MCPStatusBanner
        status={mcpStatus}
        isError={statusError}
        mcpConfig={mcpConfig}
        onTurnOn={canEdit ? turnOn : undefined}
        turningOn={patch.isPending}
      />

      <CustomTabs
        value={tabIndex}
        onChange={handleTabChange}
        variant="scrollable"
        scrollButtons="auto"
        allowScrollButtonsMobile
        sx={{
          minHeight: 40,
          borderBottom: 1,
          borderColor: "divider",
          "& .MuiTab-root": {
            minHeight: 40,
            px: 0,
            mr: 3,
            textTransform: "none",
          },
        }}
      >
        {tabs.map((tab) => (
          <Tab
            key={tab.slug}
            label={tab.label}
            disabled={tab.disabled}
            aria-describedby={tab.disabled ? disabledNoticeId : undefined}
          />
        ))}
      </CustomTabs>

      {!mcpOff && (
        <Typography
          typography="s2"
          color="text.secondary"
          id={disabledNoticeId}
          sx={{ my: 2 }}
        >
          {DISABLED_MCP_NOTICE}
        </Typography>
      )}
      <Box sx={{ minWidth: 0 }}>
        {/* Do not mount disabled data components, even for direct links. */}
        {/* Wait for initial status before mounting a tab that may issue NEW requests. */}
        {waitingForStatus ? (
          <Skeleton
            variant="rounded"
            height={132}
            role="status"
            aria-label="Loading MCP status"
          />
        ) : (
          !disabled && (
            <Content
              gatewayId={gatewayId}
              {...(mcpOff ? { runtimeOff: true } : {})}
            />
          )
        )}
      </Box>
      {siemAvailable && (
        <SiemExportDrawer open={siemOpen} onClose={() => setSiemOpen(false)} />
      )}
    </GatewayPageFrame>
  );
};

export default MCPManagementSection;
