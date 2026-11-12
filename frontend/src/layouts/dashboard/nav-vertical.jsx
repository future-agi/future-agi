import React from "react";
import { useEffect, useRef } from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Drawer from "@mui/material/Drawer";

import { usePathname } from "src/routes/hooks";

import { useResponsive } from "src/hooks/use-responsive";
import { useAuthContext } from "src/auth/hooks";

import Scrollbar from "src/components/scrollbar";
import { NavSectionVertical } from "src/components/nav-section";

import { NAV } from "../config-layout";
import {
  useNavUpgradeData,
  useNavSettingsData,
  useNavDashBoardData,
  useNavGatewayData,
  useWorkspaceSettingsNav,
} from "./config-navigation";
import { useShellNavData } from "./useShellNavData";
import WorkspaceSwitcher from "./WorkspaceSwitcher/WorkspaceSwitcher";
import { useSettingsOpen, syncSettingsOpen } from "./states";
import NavBrandRow from "src/sections/omega/shell/NavBrandRow";
import {
  hostReturnPath,
  isGatewayPath,
} from "src/sections/omega/shell/products";
import GatewayRail from "src/sections/gateway/shell/GatewayRail";
import GatewayRailScroll from "src/sections/gateway/shell/GatewayRailScroll";

import { Button, IconButton, Typography } from "@mui/material";
import SVGColor from "src/components/svg-color";
import { useNavigate } from "react-router";

// ----------------------------------------------------------------------

// The Settings panel's list (the Settings menu, or one workspace's settings)
// takes the height left under its back button and scrolls there, so a short
// window or a long "Your Workspaces" group never cuts a row off.
const PANEL_LIST_SX = {
  flex: 1,
  minHeight: 0,
  overflowX: "hidden",
  overflowY: "auto",
};

export default function NavVertical({
  openNav,
  onCloseNav,
  temporary = false,
}) {
  const { user } = useAuthContext();
  const pathname = usePathname();
  const mdUp = useResponsive("up", "md");
  const navigate = useNavigate();
  const navData = useShellNavData();
  const navUpgradeData = useNavUpgradeData();
  const navSettingsData = useNavSettingsData();
  const navDashboardData = useNavDashBoardData();
  const navGatewayData = useNavGatewayData();
  const { settingOpen, setSettingOpen } = useSettingsOpen();
  // On /dashboard/gateway/* the AI Gateway is the open product: its rail
  // replaces the Future AGI nav (the product switcher on top switches back).
  const onGateway = isGatewayPath(pathname);

  // Detect workspace-specific settings route
  const wsMatch = pathname.match(
    /\/dashboard\/settings\/workspace\/([0-9a-f-]{36})/,
  );
  const activeWorkspaceId = wsMatch ? wsMatch[1] : null;
  const workspaceNav = useWorkspaceSettingsNav(activeWorkspaceId);

  // Close the drawer after a navigation, not the moment it opens.
  const lastPathnameRef = useRef(pathname);
  useEffect(() => {
    if (lastPathnameRef.current === pathname) return;
    lastPathnameRef.current = pathname;
    if (openNav) {
      onCloseNav();
    }
  }, [onCloseNav, openNav, pathname]);

  // Keep the gateway rail; elsewhere the settings panel follows the URL both ways.
  useEffect(() => {
    if (isGatewayPath(pathname)) {
      setSettingOpen(false);
    } else {
      syncSettingsOpen(pathname, setSettingOpen);
    }
  }, [pathname, setSettingOpen]);

  const renderContent = (
    <Box
      component="nav"
      id="dashboard-navigation"
      aria-label="Main navigation"
      sx={{
        height: 1,
        width: 1,
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
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
        >
          <NavBrandRow showCollapse={!temporary && mdUp} />
          {(temporary || !mdUp) && (
            <IconButton
              aria-label="Close navigation"
              onClick={onCloseNav}
              sx={{ mr: 1 }}
            >
              <SVGColor src="/assets/icons/ic_close.svg" />
            </IconButton>
          )}
        </Stack>
        {onGateway && (
          <Box sx={{ px: 1 }}>
            <Button
              onClick={() => navigate(hostReturnPath())}
              startIcon={
                <SVGColor
                  src="/assets/icons/custom/lucide--chevron-right.svg"
                  sx={{ transform: "rotate(180deg)" }}
                  width={16}
                  height={16}
                />
              }
              size="small"
              sx={{ color: "text.secondary" }}
            >
              Back to Future AGI
            </Button>
          </Box>
        )}
        <WorkspaceSwitcher />
      </Stack>

      {/* Below the switcher: the app navigation, and the Settings panel that
          replaces it on Settings pages. The panel is sized to this box, not
          to the whole sidebar, so it never lies over the brand row or the
          workspace switcher. */}
      <Box
        sx={{
          position: "relative",
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {!onGateway && (
          <NavSectionVertical
            label="Home"
            data={navDashboardData}
            slotProps={{
              currentRole: user?.role,
              gap: 0,
            }}
            sx={{ flexShrink: 0, pt: "4px" }}
          />
        )}

        {/* Scrollable Navigation Content */}
        {onGateway ? (
          // The rail fades at the bottom while more items are below.
          <GatewayRailScroll>
            <GatewayRail groups={navGatewayData} />
          </GatewayRailScroll>
        ) : (
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
                label="Main"
                data={navData}
                slotProps={{
                  currentRole: user?.role,
                  gap: 0,
                }}
              />
            </Scrollbar>
          </Box>
        )}

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
            label="Resources"
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
              <Box sx={PANEL_LIST_SX}>
                <NavSectionVertical
                  label="Workspace settings"
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
              <Box sx={PANEL_LIST_SX}>
                <NavSectionVertical
                  label="Settings"
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
    </Box>
  );

  // Narrow Omega, gateway and compact Settings shells reserve no drawer width.
  if (temporary || !mdUp) {
    return (
      <Drawer
        open={!!openNav}
        onClose={onCloseNav}
        PaperProps={{
          sx: { width: NAV.W_VERTICAL - 40, maxWidth: "calc(100vw - 32px)" },
        }}
      >
        {renderContent}
      </Drawer>
    );
  }

  return (
    // The sidebar is one landmark (a11y "region"): the logo link and the
    // workspace switcher sit in it with the named nav landmarks.
    <Box
      component="aside"
      aria-label="Sidebar"
      sx={{
        flexShrink: 0,
        width: NAV.W_VERTICAL - 40,
        position: "relative",
        bgcolor: "background.default",
        height: "calc(100dvh - var(--fa-proto-label-h, 0px))",
      }}
    >
      {renderContent}
    </Box>
  );
}

NavVertical.propTypes = {
  openNav: PropTypes.bool,
  onCloseNav: PropTypes.func,
  temporary: PropTypes.bool,
};
