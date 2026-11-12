import React, { Suspense, useEffect, useState } from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import { Button, IconButton, SvgIcon, Typography } from "@mui/material";

import { useBoolean } from "src/hooks/use-boolean";
import { useResponsive } from "src/hooks/use-responsive";

import { useSettingsContext } from "src/components/settings";
import { usePathname, useRouter } from "src/routes/hooks";
import { isOmegaConsoleEnabled } from "src/sections/omega/shell/flags";
import {
  isAgentInfrastructurePath,
  isGatewayPath,
  rememberHostPath,
} from "src/sections/omega/shell/products";
import OmegaMobileBar from "src/sections/omega/shell/OmegaMobileBar";
import { HEADER } from "../config-layout";

import Main from "./main";
import NavMini from "./nav-mini";
import NavVertical from "./nav-vertical";
import NavHorizontal from "./nav-horizontal";
import { relaxesMinWidth } from "./narrow-routes";
// GS-06 (options-truth L3 G10): the Falcon sidebar (markdown, code
// highlighting) loads the first time it opens, not on every page.
const FalconAISidebar = React.lazy(
  () => import("src/sections/falcon-ai/FalconAISidebar"),
);
import FalconAIFab from "src/sections/falcon-ai/components/FalconAIFab";
import FalconAIBarButton from "src/sections/falcon-ai/components/FalconAIBarButton";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import CreateWorkspaceModal from "./WorkspaceSwitcher/CreateWorkspaceModal";
import { isPrototypeMode } from "src/utils/prototype";
import TwoFactorBanner from "src/components/two-factor-enforcement/TwoFactorBanner";
import SosModeBanner from "./sos-mode-banner";
import HostUpdateNotice from "src/components/host-update-status/HostUpdateNotice";
import { useFeatureAllowed } from "src/hooks/useCapabilities";
import LegacyParamScrub from "src/sections/common/ux/oneview/LegacyParamScrub";
import OneViewStyles from "src/sections/common/ux/oneview/OneViewStyles";
import RerunWithChangesHost, {
  routeIdsOf,
} from "src/sections/common/RunSources/RerunWithChangesHost";
import { isSurfaceShown } from "src/launch/launchSwitch";
import SkipLink from "./SkipLink";
import GatewayNavigationFocus from "src/sections/gateway/shell/GatewayNavigationFocus";
import PaletteBoundary from "src/sections/common/CommandPalette/PaletteBoundary";

// Always-mounted hosts that open on demand load after the shell paints, in
// their own chunks (perf #1): the page never waits for the ⌘K palette, the
// ? sheet or the run tray, and their bodies stay out of the entry chunk.
const GlobalKeyboard = React.lazy(
  () => import("src/sections/common/ux/GlobalKeyboard"),
);
const CommandPalette = React.lazy(
  () => import("src/sections/common/CommandPalette/CommandPalette"),
);
// Every page a person may open, listed in the palette's Go to.
const AppPageCommands = React.lazy(
  () => import("src/sections/common/CommandPalette/AppPageCommands"),
);
const RunTray = React.lazy(
  () => import("src/sections/develop-detail/Experiment/RunTray"),
);

// L6 launch switch (exp.rerun): the Rerun with changes host reads the
// experiment and the dataset runs of the URL in view as soon as it mounts,
// so on a hidden experiment URL it read the experiment before the route
// guard redirected. A launch build
// mounts it only where its dialog ships (never by default, flag ON or OFF);
// the host also gates its own reads.
export function RerunHostMount() {
  const { datasetId, experimentId } = routeIdsOf(usePathname());
  return isSurfaceShown("exp.rerun") && (datasetId || experimentId) ? (
    <RerunWithChangesHost />
  ) : null;
}

const COMPACT_SHELL_PREFIX = "/dashboard/settings";

function CompactShellBar({ onOpenNav }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        flexShrink: 0,
        px: 1,
        height: 48,
        borderBottom: (theme) => `solid 1px ${theme.palette.divider}`,
        backgroundColor: "background.paper",
      }}
    >
      <IconButton aria-label="Open navigation" onClick={onOpenNav}>
        <SvgIcon fontSize="small">
          <path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z" />
        </SvgIcon>
      </IconButton>
      <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}>
        Settings
      </Typography>
    </Box>
  );
}

CompactShellBar.propTypes = {
  onOpenNav: PropTypes.func.isRequired,
};

export default function DashboardLayout({ children }) {
  const settings = useSettingsContext();
  const { allowed: falconAllowed } = useFeatureAllowed("falcon_ai");
  const router = useRouter();
  const pathname = usePathname();
  const falconOpen = useFalconStore((s) => s.isSidebarOpen);
  // Mounted from its first open on (it keeps its conversation after closing).
  const [falconMounted, setFalconMounted] = useState(false);
  useEffect(() => {
    if (falconOpen) setFalconMounted(true);
  }, [falconOpen]);
  const pendingNavigation = useFalconStore((s) => s.pendingNavigation);
  const clearPendingNavigation = useFalconStore(
    (s) => s.clearPendingNavigation,
  );

  useEffect(() => {
    if (pendingNavigation) {
      router.push(pendingNavigation);
      clearPendingNavigation();
    }
  }, [pendingNavigation, router, clearPendingNavigation]);

  const mdUp = useResponsive("up", "md");
  const mdDown = useResponsive("down", "md");
  const nav = useBoolean();
  const { onFalse: closeNav } = nav;

  useEffect(() => {
    if (mdUp) closeNav();
  }, [mdUp, closeNav]);

  // Keep the gateway/infrastructure shell and compact Settings shell.
  const omegaEnabled = isOmegaConsoleEnabled();
  const relaxWidth =
    relaxesMinWidth(pathname) ||
    (omegaEnabled && isAgentInfrastructurePath(pathname));
  const narrow = relaxWidth && mdDown;
  const onHub = /^\/dashboard\/hub(\/|$)/.test(pathname);
  // The host-only horizontal bar has no gateway destinations or page finder.
  // Override the rendered layout, not the user's saved preference.
  const isHorizontal =
    settings.themeLayout === "horizontal" && !isGatewayPath(pathname);
  const isMini = settings.themeLayout === "mini";
  const compactShell =
    mdDown &&
    !isHorizontal &&
    pathname.startsWith(COMPACT_SHELL_PREFIX) &&
    !(omegaEnabled && isAgentInfrastructurePath(pathname));
  const falconInBar = narrow && !compactShell && !onHub;

  useEffect(() => {
    rememberHostPath(pathname);
  }, [pathname]);

  let navComponent;
  if (narrow || compactShell) {
    navComponent = (
      <NavVertical temporary openNav={nav.value} onCloseNav={nav.onFalse} />
    );
  } else if (isHorizontal) {
    navComponent = mdUp ? (
      <NavHorizontal />
    ) : (
      <NavVertical openNav={nav.value} onCloseNav={nav.onFalse} />
    );
  } else if (isMini) {
    navComponent = mdUp ? (
      <NavMini />
    ) : (
      <NavVertical openNav={nav.value} onCloseNav={nav.onFalse} />
    );
  } else {
    navComponent = <NavVertical openNav={nav.value} onCloseNav={nav.onFalse} />;
  }

  const boxStyles = {
    minHeight: "100dvh",
    minWidth: 0,
    width: 1,
    display: "flex",
    flexDirection: { xs: "column", md: isHorizontal ? "column" : "row" },
    backgroundColor: "background.default",
  };

  return (
    <GatewayNavigationFocus pathname={pathname}>
      <SkipLink />
      <TwoFactorBanner />
      {!isPrototypeMode() && <CreateWorkspaceModal />}
      <SosModeBanner />
      {compactShell ? (
        <Box
          data-shell="compact"
          sx={{
            display: "flex",
            flexDirection: "column",
            height: "100vh",
            minWidth: 0,
            backgroundColor: "background.paper",
          }}
        >
          <CompactShellBar onOpenNav={nav.onTrue} />
          {navComponent}
          <Box sx={{ display: "flex", flex: 1, minHeight: 0, minWidth: 0 }}>
            <Main
              id="main-content"
              tabIndex={-1}
              sx={{ height: "auto", minWidth: 0 }}
            >
              <HostUpdateNotice />
              {children}
            </Main>
          </Box>
          {falconAllowed && falconMounted && (
            <Suspense fallback={null}>
              <FalconAISidebar variant="overlay" />
            </Suspense>
          )}
        </Box>
      ) : (
        <Box sx={boxStyles}>
          {!mdUp && !narrow && (
            <Box
              component="header"
              sx={{
                height: HEADER.H_MOBILE,
                flexShrink: 0,
                display: "flex",
                alignItems: "center",
                px: 2,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Button
                color="inherit"
                aria-label="Open navigation"
                aria-controls="dashboard-navigation"
                aria-expanded={nav.value}
                onClick={nav.onTrue}
                startIcon={
                  <SvgIcon fontSize="small">
                    <path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z" />
                  </SvgIcon>
                }
              >
                Menu
              </Button>
            </Box>
          )}
          {navComponent}
          <Main id="main-content" tabIndex={-1}>
            <HostUpdateNotice />
            {narrow && (
              <OmegaMobileBar
                onOpenNav={nav.onTrue}
                end={
                  falconInBar && falconAllowed ? <FalconAIBarButton /> : null
                }
              />
            )}
            {children}
          </Main>
          {falconAllowed && !onHub && falconMounted && (
            <Suspense fallback={null}>
              <FalconAISidebar />
            </Suspense>
          )}
        </Box>
      )}
      {!onHub && <FalconAIFab docked={falconInBar} />}
      {/* One view: legacy `ui=` links scrubbed; ⌘K, the ? sheet, the run
          tray (P3). */}
      <OneViewStyles />
      <LegacyParamScrub />
      <Suspense fallback={null}>
        <GlobalKeyboard />
        <CommandPalette />
        <RunTray />
      </Suspense>
      <PaletteBoundary>
        <Suspense fallback={null}>
          <AppPageCommands />
        </Suspense>
      </PaletteBoundary>
      {/* R4-F frozen line: the Rerun with changes host (lane Ra fills it) */}
      <RerunHostMount />
    </GatewayNavigationFocus>
  );
}

DashboardLayout.propTypes = {
  children: PropTypes.node,
};
