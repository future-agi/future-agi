import React, { useEffect } from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";

import { useBoolean } from "src/hooks/use-boolean";
import { useResponsive } from "src/hooks/use-responsive";

import { useSettingsContext } from "src/components/settings";
import { usePathname, useRouter } from "src/routes/hooks";
import { paths } from "src/routes/paths";

import Main from "./main";
import { dashboardFrameSx, PHONE_BAR_HEIGHT } from "./frame";
import NavMini from "./nav-mini";
import NavVertical from "./nav-vertical";
import NavHorizontal from "./nav-horizontal";
import NavGatewayPanel from "./NavGatewayPanel";
import FalconAISidebar from "src/sections/falcon-ai/FalconAISidebar";
import FalconAIFab from "src/sections/falcon-ai/components/FalconAIFab";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import CreateWorkspaceModal from "./WorkspaceSwitcher/CreateWorkspaceModal";
import TwoFactorBanner from "src/components/two-factor-enforcement/TwoFactorBanner";
import HostUpdateNotice from "src/components/host-update-status/HostUpdateNotice";
import { IconButton, Stack, Typography } from "@mui/material";
import { addIcon } from "@iconify/react";
import Iconify from "src/components/iconify";
import Logo from "src/components/logo";
import { ShowComponent } from "../../components/show";
import { useFeatureAllowed } from "src/hooks/useCapabilities";

// Bundled locally so the phone navigation control never depends on the icon CDN.
addIcon("solar:hamburger-menu-linear", {
  width: 24,
  height: 24,
  body: '<path fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5" d="M20 7H4m16 5H4m16 5H4"/>',
});

// ----------------------------------------------------------------------

export default function DashboardLayout({ children }) {
  const settings = useSettingsContext();
  const isSOSMode = localStorage.getItem("sosMode");
  const { allowed: falconAllowed } = useFeatureAllowed("falcon_ai");
  const router = useRouter();
  const pendingNavigation = useFalconStore((s) => s.pendingNavigation);
  const clearPendingNavigation = useFalconStore(
    (s) => s.clearPendingNavigation,
  );

  // Handle navigation requests from Falcon AI agent
  useEffect(() => {
    if (pendingNavigation) {
      router.push(pendingNavigation);
      clearPendingNavigation();
    }
  }, [pendingNavigation, router, clearPendingNavigation]);

  const lgUp = useResponsive("up", "md");

  const nav = useBoolean();

  const isHorizontal = settings.themeLayout === "horizontal";

  const isMini = settings.themeLayout === "mini";

  let navComponent;
  if (isHorizontal) {
    navComponent = lgUp ? (
      <NavHorizontal />
    ) : (
      <NavVertical openNav={nav.value} onCloseNav={nav.onFalse} />
    );
  } else if (isMini) {
    navComponent = lgUp ? (
      <NavMini />
    ) : (
      <NavVertical openNav={nav.value} onCloseNav={nav.onFalse} />
    );
  } else {
    navComponent = <NavVertical openNav={nav.value} onCloseNav={nav.onFalse} />;
  }

  // Skills pages also contain and clip absolutely positioned content, so it
  // can never scroll the shell (see dashboardFrameSx).
  const pathname = usePathname();
  const skillsRoot = paths.dashboard.skills.root;
  const inSkills = pathname === skillsRoot || pathname.startsWith(`${skillsRoot}/`);
  const boxStyles = dashboardFrameSx({
    horizontal: isHorizontal,
    mini: isMini,
    phone: !lgUp,
    contain: inSkills,
  });

  return (
    <>
      {/* Below md the sidebar is a drawer: one 56px bar with the menu control and the mark, no text. */}
      {!lgUp && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ height: PHONE_BAR_HEIGHT, flexShrink: 0, px: 1, bgcolor: "background.paper", borderBottom: 1, borderColor: "divider" }}
        >
          <IconButton onClick={nav.onTrue} aria-label="Open navigation" sx={{ color: "text.primary" }}>
            <Iconify icon="solar:hamburger-menu-linear" width={24} />
          </IconButton>
          <Logo disabledLink aria-hidden="true" width={28} height={28} sx={{ width: 28, height: 28, alignItems: "center" }} />
        </Stack>
      )}
      <TwoFactorBanner />
      <CreateWorkspaceModal />
      <ShowComponent condition={isSOSMode}>
        <Box
          sx={{
            position: "absolute",
            top: 0,
            left: "50%",
            transform: "translateX(-50%)",
            background: "status.warning.solid",
            padding: 0.5,
            borderRadius: "4px",
            zIndex: 1000,
          }}
        >
          <Typography
            fontSize={12}
            fontWeight="fontWeightSemiBold"
            color="status.warning.onSolid"
          >
            🚨 You are in SOS Mode, and have access to user account please be
            careful and avoid making any drastic changes 🚨
          </Typography>
        </Box>
      </ShowComponent>
      <Box sx={boxStyles}>
        {navComponent}
        <NavGatewayPanel />
        {/* The phone bar sits above the scrolling panel; the panel fills the frame under it. */}
        <Main sx={lgUp ? undefined : { height: `calc(100vh - ${PHONE_BAR_HEIGHT}px)` }}>
          <HostUpdateNotice />
          {children}
        </Main>
        {falconAllowed && <FalconAISidebar />}
      </Box>
      <FalconAIFab />
    </>
  );
}

DashboardLayout.propTypes = {
  children: PropTypes.node,
};
