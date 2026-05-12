import React, { useEffect } from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";

import { useBoolean } from "src/hooks/use-boolean";
import { useResponsive } from "src/hooks/use-responsive";

import { useSettingsContext } from "src/components/settings";
import { useRouter } from "src/routes/hooks";

import Main from "./main";
import NavMini from "./nav-mini";
import NavVertical from "./nav-vertical";
import NavHorizontal from "./nav-horizontal";
import NavGatewayPanel from "./NavGatewayPanel";
import FalconAISidebar from "src/sections/falcon-ai/FalconAISidebar";
import FalconAIFab from "src/sections/falcon-ai/components/FalconAIFab";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import CreateWorkspaceModal from "./WorkspaceSwitcher/CreateWorkspaceModal";
import TwoFactorBanner from "src/components/two-factor-enforcement/TwoFactorBanner";
import { Typography } from "@mui/material";
import { ShowComponent } from "../../components/show";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

// ----------------------------------------------------------------------

export default function DashboardLayout({ children }) {
  const settings = useSettingsContext();
  const isSOSMode = localStorage.getItem("sosMode");
  const { isOSS } = useDeploymentMode();
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

  const lgUp = useResponsive("up", "xs");

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

  const boxStyles = isHorizontal
    ? {
        display: "block",
      }
    : {
        minHeight: 1,
        minWidth: 1200,
        display: "flex",
        flexDirection: { xs: "row", lg: "row" },
        backgroundColor: "background.paper",
        ...(isMini ? {} : { overflowX: "auto" }),
      };

  return (
    <>
      {/* <Header onOpenNav={nav.onTrue} /> */}
      <TwoFactorBanner />
      <CreateWorkspaceModal />
      <ShowComponent condition={isSOSMode}>
        <Box
          sx={{
            position: "absolute",
            top: 0,
            left: "50%",
            transform: "translateX(-50%)",
            background: "#FFD94D",
            padding: 0.5,
            borderRadius: 0.5,
            zIndex: 1000,
          }}
        >
          <Typography fontSize={10} fontWeight="fontWeightSemiBold">
            🚨 You are in SOS Mode, and have access to user account please be
            careful and avoid making any drastic changes 🚨
          </Typography>
        </Box>
      </ShowComponent>
      <Box sx={boxStyles}>
        {navComponent}
        <NavGatewayPanel />
        <Main>{children}</Main>
        {!isOSS && <FalconAISidebar />}
      </Box>
      <FalconAIFab />
    </>
  );
}

DashboardLayout.propTypes = {
  children: PropTypes.node,
};
