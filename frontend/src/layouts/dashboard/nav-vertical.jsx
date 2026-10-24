import React from "react";
import { useEffect } from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Drawer from "@mui/material/Drawer";

import { usePathname } from "src/routes/hooks";

import { useResponsive } from "src/hooks/use-responsive";
import { useAuthContext } from "src/auth/hooks";

import Logo from "src/components/logo";
import Scrollbar from "src/components/scrollbar";
import { NavSectionVertical } from "src/components/nav-section";

import { NAV } from "../config-layout";
import {
  useNavData,
  useNavUpgradeData,
  useNavSettingsData,
  useNavDashBoardData,
  useWorkspaceSettingsNav,
} from "./config-navigation";
import WorkspaceSwitcher from "./WorkspaceSwitcher/WorkspaceSwitcher";
import { useSettingsOpen, useGatewayOpen } from "./states";

import { Button, Typography } from "@mui/material";
import SVGColor from "src/components/svg-color";
import { useNavigate } from "react-router";

// ----------------------------------------------------------------------

export default function NavVertical({ openNav, onCloseNav }) {
  const { user } = useAuthContext();
  const pathname = usePathname();
  const lgUp = useResponsive("up", "xs");
  const navigate = useNavigate();
  const navData = useNavData();
  const navUpgradeData = useNavUpgradeData();
  const navSettingsData = useNavSettingsData();
  const navDashboardData = useNavDashBoardData();
  const { settingOpen, setSettingOpen } = useSettingsOpen();

  // Detect workspace-specific settings route
  const wsMatch = pathname.match(
    /\/dashboard\/settings\/workspace\/([0-9a-f-]{36})/,
  );
  const activeWorkspaceId = wsMatch ? wsMatch[1] : null;
  const workspaceNav = useWorkspaceSettingsNav(activeWorkspaceId);
  const { gatewayOpen, setGatewayOpen } = useGatewayOpen();

  useEffect(() => {
    if (openNav) {
      onCloseNav();
    }
  }, [onCloseNav, openNav, pathname]);

  // Sync settings sidebar visibility with the URL — on reload, and when a
  // settings page links out to the app (e.g. GitHub → Agents → New agent),
  // so the app's own sidebar comes back instead of the settings one.
  useEffect(() => {
    if (pathname.includes("/settings/")) {
      setSettingOpen(true);
    } else if (settingOpen) {
      setSettingOpen(false);
    }
  }, [pathname, setSettingOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-open gateway panel when on gateway routes, close when leaving
  useEffect(() => {
    const isGatewayRoute = pathname.startsWith("/dashboard/gateway");
    if (isGatewayRoute && !gatewayOpen && !settingOpen) {
      setGatewayOpen(true);
    } else if (!isGatewayRoute && gatewayOpen) {
      setGatewayOpen(false);
    }
  }, [pathname, gatewayOpen, settingOpen, setGatewayOpen]);

  const renderContent = (
    <Box
      sx={{
        height: 1,
        width: NAV.W_VERTICAL - 40,
        position: "relative",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        borderRight: (theme) => `1px solid ${theme.palette.divider}`,
        bgcolor: "background.default",
      }}
    >
      {/* Fixed Header */}
      <Stack
        sx={{
          flexShrink: 0,
          display: "flex",
          flexDirection: "column",
          gap: "4px",
          pt: "8px",
        }}
      >
        <Logo sx={{ mb: 0 }} />
        <WorkspaceSwitcher />
        <NavSectionVertical
          data={navDashboardData}
          slotProps={{
            currentRole: user?.role,
            gap: 0,
          }}
        />
      </Stack>

      {/* Scrollable Navigation Content */}
      <Box sx={{ flex: 1, overflow: "hidden" }}>
        <Scrollbar
          sx={{
            height: 1,
            "& .simplebar-content": {
              height: "auto",
              minHeight: 1,
              display: "flex",
              flexDirection: "column",
              paddingBottom: 0.5,
            },
          }}
        >
          <NavSectionVertical
            data={navData}
            slotProps={{
              currentRole: user?.role,
              gap: 0,
            }}
          />
        </Scrollbar>
      </Box>

      {/* Fixed Bottom Section */}
      <Box
        sx={{
          flexShrink: 0,
          bgcolor: "background.default",
          borderTop: (theme) => `1px solid ${theme.palette.border.subtle}`,
          pt: 1,
          pb: 1,
        }}
      >
        <NavSectionVertical
          data={navUpgradeData}
          slotProps={{
            currentRole: user?.role,
            gap: 0,
          }}
          isBottomSection={true}
        />
      </Box>

      {/* Settings Panel */}
      <Box
        sx={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          bgcolor: "background.default",
          transform: settingOpen ? "translateX(0)" : "translateX(100%)",
          visibility: settingOpen ? "visible" : "hidden",
          pointerEvents: settingOpen ? "auto" : "none",
          transition: (theme) =>
            theme.transitions.create(["transform", "visibility"], {
              duration: theme.transitions.duration.shorter,
              easing: theme.transitions.easing.easeInOut,
            }),
          gap: 1,
        }}
      >
        {workspaceNav ? (
          <>
            <Box sx={{ padding: 1 }}>
              <Box
                sx={{
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  display: "flex",
                  alignItems: "center",
                  paddingBottom: 1,
                }}
              >
                <Button
                  onClick={() =>
                    navigate("/dashboard/settings/profile-settings")
                  }
                  startIcon={
                    <SVGColor
                      src="/assets/icons/custom/lucide--chevron-right.svg"
                      sx={{ transform: "rotate(180deg)" }}
                      width={20}
                      height={20}
                    />
                  }
                  size="small"
                >
                  Back to Settings
                </Button>
              </Box>
              <Typography
                variant="subtitle1"
                fontWeight={600}
                sx={{ mt: 1.5, px: 1.5 }}
              >
                {workspaceNav.name}
              </Typography>
            </Box>
            <Box sx={{ flex: 1, overflow: "hidden" }}>
              <NavSectionVertical
                data={workspaceNav.data}
                slotProps={{
                  currentRole: user?.role,
                  gap: 0,
                }}
                sx={{
                  paddingTop: 1,
                }}
              />
            </Box>
          </>
        ) : (
          <>
            <Box sx={{ padding: 1 }}>
              <Box
                sx={{
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  display: "flex",
                  alignItems: "center",
                  paddingBottom: 1,
                }}
              >
                <Button
                  onClick={() => {
                    const appRedirectUrl = localStorage.getItem(
                      "redirect-url-from-settings",
                    );
                    navigate(appRedirectUrl || "/dashboard/develop");
                    setSettingOpen(false);
                    localStorage.removeItem("redirect-url-from-settings");
                  }}
                  startIcon={
                    <SVGColor
                      src="/assets/icons/custom/lucide--chevron-right.svg"
                      sx={{ transform: "rotate(180deg)" }}
                      width={20}
                      height={20}
                    />
                  }
                  size="small"
                >
                  Back to app
                </Button>
              </Box>
            </Box>
            <Box sx={{ flex: 1, overflow: "hidden" }}>
              <NavSectionVertical
                data={navSettingsData}
                slotProps={{
                  currentRole: user?.role,
                  gap: 0,
                }}
                sx={{
                  paddingTop: 1.5,
                }}
              />
            </Box>
          </>
        )}
      </Box>
    </Box>
  );

  return (
    <Box
      sx={{
        flexShrink: { xs: 0 },
        width: { xs: NAV.W_VERTICAL - 40 },
        position: "relative",
        backgroundColor: "background.default",
        height: "100vh",
      }}
    >
      {lgUp ? (
        <Stack
          sx={{
            height: 1,
            // position: "fixed",
            width: NAV.W_VERTICAL - 40,
            background: "background.paper",
            marginBottom: "1rem",
          }}
        >
          {renderContent}
        </Stack>
      ) : (
        <Drawer
          open={openNav}
          onClose={onCloseNav}
          PaperProps={{
            sx: {
              width: NAV.W_VERTICAL - 40,
            },
          }}
        >
          {renderContent}
        </Drawer>
      )}
    </Box>
  );
}

NavVertical.propTypes = {
  openNav: PropTypes.bool,
  onCloseNav: PropTypes.func,
};
