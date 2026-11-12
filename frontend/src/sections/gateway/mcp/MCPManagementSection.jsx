import React, { useCallback, useEffect, useId, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Typography from "@mui/material/Typography";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Tab from "@mui/material/Tab";
import Tooltip from "@mui/material/Tooltip";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import { paths } from "src/routes/paths";
import { useOrganization } from "src/contexts/OrganizationContext";
import { CustomTabs } from "src/components/tabs/tabs";
import { visuallyHidden } from "src/components/empty-state/useCopyCode";

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
import MCPStatusBanner, {
  MCP_ORG_OFF_LINE,
  MCP_TURN_ON_TO_TEST,
  mcpVerdict,
} from "./MCPStatusBanner";
import {
  DISABLED_MCP_NOTICE,
  MCP_TAB_PARAM,
  isVisibleMCPTab,
  resolveMCPTabIndex,
  shownMCPTabs,
} from "./tabs";
import { isMCPRuntimeOff } from "./hooks/mcpRuntimeState";
import { toolCallFeatures } from "./hooks/useMCPToolCallFeatures";
import {
  releaseShown,
  releaseSwitchesOff,
} from "./hooks/useMCPReleaseFeatures";
import SiemExportDrawer from "./calls/SiemExportDrawer";
import PlaygroundTab from "./playground/PlaygroundTab";

// The MCP page: the FA page frame, the MCP verdict (a chip, and a banner when
// something needs attention), the tab bar and the open tab. The open tab
// lives in the query string (`?tab=<slug>`); a link that still carries it in
// the path (`/gateway/mcp/<slug>`) opens the same tab. Deployment-off uses
// the baseline allowlist, with saved edits and no NEW surfaces. Personal
// client grants remain independently revocable when client OAuth is enabled.
// The bar's tabs follow deployment capabilities
// (tabs.js shownMCPTabs).

// A tab the deployment has off stays in the bar, disabled, and says so where
// the person is: aria-disabled instead of the disabled attribute keeps hover
// and keyboard focus (Tab key) on it, so its tooltip shows the line, and a
// screen reader hears the line as the tab's description.
function OffNoticeTab({ notice, ...props }) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  return (
    <Tooltip
      title={notice}
      describeChild
      disableFocusListener
      open={hovered || focused}
      onOpen={() => setHovered(true)}
      onClose={() => setHovered(false)}
    >
      <Tab
        {...props}
        aria-disabled="true"
        tabIndex={0}
        disableRipple
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        sx={{ color: "text.disabled", cursor: "default" }}
      />
    </Tooltip>
  );
}

OffNoticeTab.propTypes = { notice: PropTypes.string.isRequired };

const MCPManagementSection = () => {
  const { gatewayId } = useGatewayContext();
  const { currentOrganizationId } = useOrganization();
  // Drafts and answers live only in this mounted session, never in URLs or
  // browser storage. A different organization or gateway starts a new session.
  return <MCPManagementSession key={JSON.stringify([currentOrganizationId, gatewayId])} gatewayId={gatewayId} />;
};

const MCPManagementSession = ({ gatewayId }) => {
  const { tab: pathSlug } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { data: config } = useGatewayConfig(gatewayId);
  const {
    data: mcpStatus,
    isError: statusError,
    isLoading: statusLoading,
    refetch: refetchStatus,
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
  // Credentials, Virtual servers, Connect, Tool calls and MCP clients stay in
  // the bar while the status loads or fails; a switch that is off keeps its
  // tab, disabled.
  const statusRead = {
    data: mcpStatus,
    isError: statusError,
    isLoading: statusLoading,
  };
  const { credentialVault, virtualServers } = releaseShown(statusRead);
  const switchesOff = releaseSwitchesOff(statusRead);
  const credentialVaultOff = switchesOff.credentialVault;
  const virtualServersOff = switchesOff.virtualServers;
  const mcpConfig = config ? config.mcp || {} : undefined;
  const verdict = mcpVerdict({
    status: mcpStatus,
    isError: statusError,
    mcpConfig,
  });
  // The banner says MCP is off for this organization; each tab says what
  // adding does there. The banner's Turn on MCP stays the one action. Try a
  // tool cannot run while MCP is off (the gateway lists no tools), so its tab
  // stays in the bar, disabled, with the reason. Test connection works.
  const orgOff = verdict.kind === "org_off";
  const tabs = useMemo(() => {
    const shown = shownMCPTabs({
      clientOAuth,
      clientInstalls,
      toolCallExplorer,
      credentialVault,
      virtualServers,
      credentialVaultOff,
      virtualServersOff,
      runtimeOff: mcpOff,
    });
    if (!orgOff) return shown;
    return shown.map((tab) =>
      tab.slug === "playground" && !tab.disabled
        ? { ...tab, disabled: true, offNotice: MCP_TURN_ON_TO_TEST }
        : tab,
    );
  }, [
    clientOAuth,
    clientInstalls,
    toolCallExplorer,
    credentialVault,
    virtualServers,
    credentialVaultOff,
    virtualServersOff,
    mcpOff,
    orgOff,
  ]);
  const requestedSlug = searchParams.get(MCP_TAB_PARAM) ?? pathSlug;
  const tabIndex = resolveMCPTabIndex(requestedSlug, tabs);
  const { Content, disabled, slug, offNotice } = tabs[tabIndex];
  const playgroundActive = slug === "playground" && !disabled && !waitingForStatus;
  const [playgroundVisited, setPlaygroundVisited] = useState(false);
  useEffect(() => {
    if (playgroundActive) setPlaygroundVisited(true);
  }, [playgroundActive]);
  const disabledNoticeId = useId();
  const offNoticeId = useId();
  const offTabNoticeId = useId();
  const turnOnNoticeId = useId();
  // An off tab's description is the page's line when that line is its own
  // notice, else a visually hidden copy of its notice, so its target is
  // always in the DOM.
  const offNoticeShown = Boolean(offNotice) && !waitingForStatus;
  const shownNotice = offNoticeShown ? offNotice : null;
  const hiddenNoticeId = (notice) =>
    notice === MCP_TURN_ON_TO_TEST ? turnOnNoticeId : offTabNoticeId;
  const offTabDescription = (notice) =>
    notice === shownNotice ? offNoticeId : hiddenNoticeId(notice);
  const hiddenNotices = [
    ...new Set(tabs.map((tab) => tab.offNotice).filter(Boolean)),
  ].filter((notice) => notice !== shownNotice);
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
        onRetry={refetchStatus ? () => refetchStatus() : undefined}
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
          // Tab padding and gaps come from the theme, as on Providers.
          "& .MuiTab-root": {
            minHeight: 40,
            textTransform: "none",
          },
        }}
      >
        {tabs.map((tab) =>
          tab.offNotice ? (
            <OffNoticeTab
              key={tab.slug}
              label={tab.label}
              notice={tab.offNotice}
              aria-describedby={offTabDescription(tab.offNotice)}
            />
          ) : (
            <Tab
              key={tab.slug}
              label={tab.label}
              disabled={tab.disabled}
              aria-describedby={tab.disabled ? disabledNoticeId : undefined}
            />
          ),
        )}
      </CustomTabs>
      {hiddenNotices.map((notice) => (
        <Box
          key={notice}
          component="span"
          id={hiddenNoticeId(notice)}
          sx={visuallyHidden}
        >
          {notice}
        </Box>
      ))}

      {!mcpOff && (
        <Typography
          typography="s2"
          color="text.secondary"
          id={disabledNoticeId}
          sx={{ my: 0 }}
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
          <>
            {orgOff && (
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="mcp-org-off-line"
                sx={{ mb: offNotice ? 0.5 : 1.5 }}
              >
                {MCP_ORG_OFF_LINE}
              </Typography>
            )}
            {offNotice && (
              <Typography
                typography="s2"
                color="text.secondary"
                id={offNoticeId}
                data-testid="mcp-tab-off-notice"
              >
                {offNotice}
              </Typography>
            )}
            {!disabled && !playgroundActive && (
              <Content
                gatewayId={gatewayId}
                {...(mcpOff ? { runtimeOff: true } : {})}
              />
            )}
            {/* Keep the visited editor and its mutation observer alive across
                tabs. Hidden editors do not poll, execute or expose controls. */}
            {(playgroundActive || playgroundVisited) && (
              <Box hidden={!playgroundActive}>
                <PlaygroundTab
                  gatewayId={gatewayId}
                  active={playgroundActive}
                  runtimeOff={mcpOff}
                />
              </Box>
            )}
          </>
        )}
      </Box>
      {siemAvailable && (
        <SiemExportDrawer open={siemOpen} onClose={() => setSiemOpen(false)} />
      )}
    </GatewayPageFrame>
  );
};

MCPManagementSession.propTypes = { gatewayId: PropTypes.string };

export default MCPManagementSection;
