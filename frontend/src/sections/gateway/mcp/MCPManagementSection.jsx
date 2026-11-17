import React, {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import Typography from "@mui/material/Typography";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Tab from "@mui/material/Tab";
import { tabsClasses } from "@mui/material/Tabs";
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
import { confirmedTurnOn } from "./turnOnToast";
import McpIcon from "./components/McpIcon";
import MCPStatusBanner, {
  MCP_ORG_OFF_HINT,
  MCP_ORG_OFF_LINE,
  MCP_TURN_ON_TO_TEST,
  mcpVerdict,
} from "./MCPStatusBanner";
import TurnOffMCPDialog from "./TurnOffMCPDialog";
import { isMCPEnabled } from "./utils";
import {
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

// A tab MCP being off for the organization holds back. It opens as before;
// the tab row's org-off rule dims it while it is not the open tab.
const ORG_OFF_TAB_CLASS = "mcp-tab-org-off";
const OFF_TAB_SELECTOR = [
  "& .MuiTab-root.Mui-disabled",
  '& .MuiTab-root[aria-disabled="true"]',
].join(", ");
const ORG_OFF_TAB_SELECTOR = `& .MuiTab-root.${ORG_OFF_TAB_CLASS}:not(.Mui-selected)`;

// The row wraps instead of scrolling, so every tab stays in view at any width
// (twelve tabs overflow the column at 1024 px). The open tab keeps the
// console's sliding underline (the theme's, as on every gateway tab row).
const TAB_ROW_SX = {
  minHeight: 40,
  borderBottom: 1,
  borderColor: "divider",
  "& .MuiTabs-flexContainer": { flexWrap: "wrap" },
  // Tab padding and gaps come from the theme, as on Providers.
  "& .MuiTab-root": {
    minHeight: 40,
    textTransform: "none",
  },
  // An off tab reads as off and stays legible. Without this the theme's
  // colour for an unselected tab (and its hover) wins, so an off tab looks
  // like any other, and MUI's disabled fade dims a disabled label twice.
  [OFF_TAB_SELECTOR]: {
    color: "text.disabled",
    opacity: 1,
    "&:hover": { color: "text.disabled" },
  },
  // A tab that still opens keeps text contrast (tertiary, 4.5:1 or better),
  // dimmer than the other tabs; once open it reads as the selected tab.
  [ORG_OFF_TAB_SELECTOR]: {
    color: "text.tertiary",
    opacity: 1,
    "&:hover": { color: "text.tertiary" },
  },
};

// MUI draws the sliding underline at the bottom of the whole row. Before the
// row mounts it sits inside the open tab, so only the row's own underline is
// moved.
const ROW_UNDERLINE_SELECTOR = `& .${tabsClasses.scroller} > .${tabsClasses.indicator}`;

// How far the underline rises so it sits under the open tab when the row
// wraps and that tab is not on the last line; 0 on a one-line row. Measured
// again when the open tab or the tabs change, and when a tab or the row
// changes size (a narrower column, a font that loads late).
function useUnderlineLift(rowRef, tabIndex, tabs) {
  const [lift, setLift] = useState(0);
  useLayoutEffect(() => {
    const row = rowRef.current;
    if (!row) return undefined;
    const measure = () => {
      const scroller = row.querySelector(`.${tabsClasses.scroller}`);
      const open = row.querySelector('[role="tab"][aria-selected="true"]');
      if (!scroller || !open) {
        setLift(0);
        return;
      }
      const openBottom = open.offsetTop + open.offsetHeight;
      // The same value again does not render again.
      setLift(Math.max(0, scroller.clientHeight - openBottom));
    };
    measure();
    const list = row.querySelector('[role="tablist"]');
    if (!list || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    [list, ...list.children].forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [rowRef, tabIndex, tabs]);
  return lift;
}

// A tab the deployment has off (or Try a tool while MCP is off for the
// organization) stays in the bar, disabled, and says so where the person is:
// aria-disabled instead of the disabled attribute keeps hover and keyboard
// focus (Tab key) on it, so its tooltip shows the line, and a screen reader
// hears the line as the tab's description. An `openable` tab
// still opens and shows its line on hover only: a click focuses it, and a
// focus tooltip would then stay over the tab it opened.
function OffNoticeTab({ notice, openable = false, ...props }) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const offProps = openable
    ? { className: ORG_OFF_TAB_CLASS }
    : {
        "aria-disabled": "true",
        tabIndex: 0,
        disableRipple: true,
        sx: { cursor: "default" },
      };
  return (
    <Tooltip
      title={notice}
      describeChild
      disableFocusListener
      open={hovered || (focused && !openable)}
      onOpen={() => setHovered(true)}
      onClose={() => setHovered(false)}
    >
      <Tab
        {...props}
        {...offProps}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      />
    </Tooltip>
  );
}

OffNoticeTab.propTypes = {
  notice: PropTypes.string.isRequired,
  openable: PropTypes.bool,
};

const MCPManagementSection = () => {
  const { gatewayId } = useGatewayContext();
  const { currentOrganizationId } = useOrganization();
  // Drafts and answers live only in this mounted session, never in URLs or
  // browser storage. A different organization or gateway starts a new session.
  return (
    <MCPManagementSession
      key={JSON.stringify([currentOrganizationId, gatewayId])}
      gatewayId={gatewayId}
    />
  );
};

const MCPManagementSession = ({ gatewayId }) => {
  const { tab: pathSlug } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { data: config, refetch: refetchConfig } = useGatewayConfig(gatewayId);
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
  // Connect still opens, dimmed, and the banner's title describes it.
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
    return shown.map((tab) => {
      if (tab.disabled) return tab;
      if (tab.slug === "playground")
        return { ...tab, disabled: true, offNotice: MCP_TURN_ON_TO_TEST };
      if (tab.slug === "connect")
        return { ...tab, orgOffNotice: MCP_ORG_OFF_HINT };
      return tab;
    });
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
  const tabRowRef = useRef(null);
  const underlineLift = useUnderlineLift(tabRowRef, tabIndex, tabs);
  const tabRowSx = useMemo(
    () =>
      underlineLift > 0
        ? {
            ...TAB_ROW_SX,
            [ROW_UNDERLINE_SELECTOR]: { bottom: `${underlineLift}px` },
          }
        : TAB_ROW_SX,
    [underlineLift],
  );
  const playgroundActive =
    slug === "playground" && !disabled && !waitingForStatus;
  const [playgroundVisited, setPlaygroundVisited] = useState(false);
  useEffect(() => {
    if (playgroundActive) setPlaygroundVisited(true);
  }, [playgroundActive]);
  const offNoticeId = useId();
  const offTabNoticeId = useId();
  const turnOnNoticeId = useId();
  // While MCP is off for the organization, the banner says so; Connect takes
  // its title as the tab's description, so the page says it once.
  const bannerTitleId = useId();
  // An off tab's description is the page's line when that line is its own
  // notice. Otherwise Try a tool's reason is a line under the tab bar, read
  // without hovering, and the deployment's line is a visually hidden copy,
  // so the target is always in the DOM.
  const offNoticeShown = Boolean(offNotice) && !waitingForStatus;
  const shownNotice = offNoticeShown ? offNotice : null;
  const turnOnReasonShown =
    shownNotice !== MCP_TURN_ON_TO_TEST &&
    tabs.some((tab) => tab.offNotice === MCP_TURN_ON_TO_TEST);
  const offTabDescription = (notice) => {
    if (notice === shownNotice) return offNoticeId;
    return notice === MCP_TURN_ON_TO_TEST ? turnOnNoticeId : offTabNoticeId;
  };
  const hiddenNotices = [
    ...new Set(tabs.map((tab) => tab.offNotice).filter(Boolean)),
  ].filter(
    (notice) => notice !== shownNotice && notice !== MCP_TURN_ON_TO_TEST,
  );
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

  // Turning MCP on or off writes the org's gateway config: Owner or Admin
  // only. Off is offered while MCP reads as on: the deployment runs it, the
  // status answered, the verdict is on (or on with no server yet), and the
  // saved switch or the gateway says on. Never beside "Gateway unreachable"
  // or "Not reported", and never together with the org-off verdict, which
  // offers Turn on MCP.
  const { canEdit } = useGatewayAdmin();
  const reloadMutation = useReloadConfig();
  const patch = useMCPConfigPatch(gatewayId);
  const canTurnOff =
    canEdit &&
    Boolean(gatewayId) &&
    !mcpOff &&
    !statusError &&
    mcpStatus !== undefined &&
    (verdict.kind === "on" || verdict.kind === "empty") &&
    (mcpConfig?.enabled === true || isMCPEnabled(mcpStatus));
  const [turnOffOpen, setTurnOffOpen] = useState(false);
  const [turnOffError, setTurnOffError] = useState(null);
  const turnOffShown = turnOffOpen && canTurnOff;
  // A save answers after the render that sent it, so its error handler reads
  // here whether the question is still on screen.
  const turnOffShownRef = useRef(turnOffShown);
  useEffect(() => {
    turnOffShownRef.current = turnOffShown;
  }, [turnOffShown]);
  // The question closes when MCP stops reading as on (for example a status
  // read fails), so it never opens again by itself when the status recovers.
  useEffect(() => {
    if (canTurnOff) return;
    setTurnOffOpen(false);
    setTurnOffError(null);
  }, [canTurnOff]);

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
        onSuccess: async (result) => {
          if (result?.gateway_synced === false) {
            enqueueSnackbar(new SavedButNotAppliedError(result).message, {
              variant: "warning",
            });
          } else {
            // What the re-read configuration and status confirm.
            const { message, variant } = await confirmedTurnOn({
              refetchConfig,
              refetchStatus,
            });
            enqueueSnackbar(message, { variant });
          }
        },
        onError: (err) =>
          enqueueSnackbar(apiErrorMessage(err, "MCP could not be turned on."), {
            variant: "error",
          }),
      },
    );
  };

  const openTurnOff = () => {
    setTurnOffError(null);
    setTurnOffOpen(true);
  };

  // The same write as turnOn, with the off value. Nothing changes on the
  // page until it lands; a failure stays in the dialog, and MCP stays on. If
  // the dialog closed while the save was pending, the failure shows as an
  // error toast, as Turn on MCP's does.
  const turnOff = () => {
    if (!canTurnOff || patch.isPending) return;
    setTurnOffError(null);
    patch.mutate(
      { enabled: false },
      {
        onSuccess: (result) => {
          setTurnOffOpen(false);
          if (result?.gateway_synced === false) {
            enqueueSnackbar(new SavedButNotAppliedError(result).message, {
              variant: "warning",
            });
          } else {
            enqueueSnackbar("MCP turned off for this organization", {
              variant: "success",
            });
          }
        },
        onError: (err) => {
          const message = apiErrorMessage(err, "MCP could not be turned off.");
          if (turnOffShownRef.current) setTurnOffError(message);
          else enqueueSnackbar(message, { variant: "error" });
        },
      },
    );
  };

  return (
    <GatewayPageFrame
      title="MCP tools"
      description={
        mcpOff ? undefined : "Manage MCP servers and saved settings."
      }
      // The SIEM action/drawer requires MCP ON AND SIEM ON, never retained OFF.
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
          {canTurnOff && (
            <Button
              variant="outlined"
              onClick={openTurnOff}
              disabled={patch.isPending}
            >
              Turn off MCP
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
        titleId={bannerTitleId}
      />

      <CustomTabs
        ref={tabRowRef}
        value={tabIndex}
        onChange={handleTabChange}
        variant="standard"
        sx={tabRowSx}
      >
        {tabs.map((tab) => {
          if (tab.offNotice)
            return (
              <OffNoticeTab
                key={tab.slug}
                label={tab.label}
                notice={tab.offNotice}
                aria-describedby={offTabDescription(tab.offNotice)}
              />
            );
          if (tab.orgOffNotice)
            return (
              <OffNoticeTab
                key={tab.slug}
                label={tab.label}
                notice={tab.orgOffNotice}
                openable
                aria-describedby={bannerTitleId}
              />
            );
          return (
            <Tab key={tab.slug} label={tab.label} disabled={tab.disabled} />
          );
        })}
      </CustomTabs>
      {hiddenNotices.map((notice) => (
        <Box
          key={notice}
          component="span"
          id={offTabNoticeId}
          sx={visuallyHidden}
        >
          {notice}
        </Box>
      ))}

      {turnOnReasonShown && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="mcp-try-tool-off-reason"
          sx={{ my: 0 }}
        >
          Try a tool:{" "}
          <Box component="span" id={turnOnNoticeId}>
            {MCP_TURN_ON_TO_TEST}
          </Box>
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
      <TurnOffMCPDialog
        open={turnOffShown}
        pending={Boolean(patch.isPending)}
        error={turnOffError}
        onCancel={() => setTurnOffOpen(false)}
        onConfirm={turnOff}
      />
    </GatewayPageFrame>
  );
};

MCPManagementSession.propTypes = { gatewayId: PropTypes.string };

export default MCPManagementSection;
