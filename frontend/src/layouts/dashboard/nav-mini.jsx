import React, { useEffect } from "react";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";

import { usePathname } from "src/routes/hooks";
import { useAuthContext } from "src/auth/hooks";

import { hideScroll } from "src/theme/css";

import { NavSectionMini } from "src/components/nav-section";

import { NAV } from "../config-layout";
import {
  useNavDashBoardData,
  useNavGatewayData,
  useNavSettingsData,
  useNavUpgradeData,
  useWorkspaceSettingsNav,
} from "./config-navigation";
import { useShellNavData } from "./useShellNavData";
import { IconButton, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import Logo from "src/components/logo";
import { useSettingsContext } from "src/components/settings";
import WorkspaceSwitcher from "./WorkspaceSwitcher/WorkspaceSwitcher";
import { useSettingsOpen, syncSettingsOpen } from "./states";
import { useNavigate } from "react-router";
import ProductSwitcher from "src/sections/omega/shell/ProductSwitcher";
import { isGatewayPath } from "src/sections/omega/shell/products";
import GatewayRail from "src/sections/gateway/shell/GatewayRail";

// ----------------------------------------------------------------------

// The Settings panel's list takes the height left under its back button and
// scrolls there (no visible bar, as the rail itself), so a short window or a
// long "Your Workspaces" group never cuts a row off.
const PANEL_LIST_SX = {
  flex: 1,
  minHeight: 0,
  overflowX: "hidden",
  ...hideScroll.y,
};

export default function NavMini() {
  const { user } = useAuthContext();
  const settings = useSettingsContext();
  const navData = useShellNavData();
  const navUpgradeData = useNavUpgradeData();

  const navDashboardData = useNavDashBoardData();

  const navSettingsData = useNavSettingsData();
  const navGatewayData = useNavGatewayData();
  const { settingOpen, setSettingOpen } = useSettingsOpen();
  const navigate = useNavigate();
  const pathname = usePathname();
  const onGateway = isGatewayPath(pathname);

  // Detect workspace-specific settings route
  const wsMatch = pathname.match(
    /\/dashboard\/settings\/workspace\/([0-9a-f-]{36})/,
  );
  const activeWorkspaceId = wsMatch ? wsMatch[1] : null;
  const workspaceNav = useWorkspaceSettingsNav(activeWorkspaceId);

  // Keep the gateway rail; elsewhere the settings panel follows the URL both ways.
  useEffect(() => {
    if (isGatewayPath(pathname)) {
      setSettingOpen(false);
    } else {
      syncSettingsOpen(pathname, setSettingOpen);
    }
  }, [pathname, setSettingOpen]);

  return (
    <Box
      sx={{
        flexShrink: { xs: 0 },
        width: { xs: NAV.W_MINI },
        backgroundColor: "background.default",
        borderRight: (theme) => `1px solid ${theme.palette.divider}`,
        paddingBottom: "16px",
        position: "relative", // important for settings panel overlay
        height: "calc(100vh - var(--fa-proto-label-h, 0px))",
        overflow: "hidden",
      }}
    >
      <Stack
        sx={{
          // pb: 2,
          height: 1,
          // position: "fixed",
          width: NAV.W_MINI,

          ...hideScroll.x,
        }}
        direction={"column"}
        alignItems={"center"}
        height={"100%"}
      >
        {/* Design system: same rhythm as the open sidebar — 8px top,
            4px between header rows, 24px rows, a hairline in place of each
            group label, and the bottom block under a subtle top border. */}
        <Stack sx={{ pt: "8px", width: 1 }} gap="4px" alignItems="center">
          <Logo collapsed />
          <IconButton
            aria-label="Expand sidebar"
            aria-expanded={settings.themeLayout !== "mini"}
            onClick={() =>
              settings.onUpdate(
                "themeLayout",
                settings.themeLayout === "vertical" ? "mini" : "vertical",
              )
            }
            size="small"
            sx={{
              color: "text.secondary",
              width: 36,
              p: 0,
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <SvgColor
              sx={{ height: "14px", width: "14px" }}
              src="/assets/icons/navbar/ic_nav_close_toggle.svg"
            />
          </IconButton>
          <ProductSwitcher collapsed />
          <WorkspaceSwitcher collapsed />
        </Stack>

        {/* Below the switcher: the rail's app navigation, and the Settings
            panel that replaces it on Settings pages. The panel is sized to
            this box, not to the whole rail, so it never lies over the logo,
            the Expand sidebar toggle or the switchers. The box grows with its
            content, so a long rail still scrolls as before. */}
        <Box
          sx={{
            position: "relative",
            flex: "1 0 auto",
            width: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            overflow: "hidden",
          }}
        >
          {!onGateway && (
            <NavSectionMini
              data={navDashboardData}
              slotProps={{ currentRole: user?.role, gap: 0 }}
              sx={{ pt: "4px" }}
            />
          )}

          {onGateway ? (
            <GatewayRail groups={navGatewayData} collapsed />
          ) : (
            <NavSectionMini
              data={navData}
              slotProps={{
                currentRole: user?.role,
                gap: 0,
              }}
            />
          )}
          <Box sx={{ flexGrow: 1 }} />

          <Stack
            alignItems="center"
            sx={{
              width: 1,
              mt: 1,
              pt: 1,
              pb: 1,
              borderTop: (theme) => `1px solid ${theme.palette.border.subtle}`,
            }}
          >
            <NavSectionMini
              data={navUpgradeData}
              slotProps={{
                currentRole: user?.role,
                gap: 0,
              }}
            />
          </Stack>

          <Box
            sx={{
              position: "absolute",
              top: 0,
              width: NAV.W_VERTICAL - 194,
              left: 6,
              height: "100%",
              display: "flex",
              flexDirection: "column",
              bgcolor: "background.default",
              transform: settingOpen ? "translateX(0)" : "translateX(100%)",
              transition: (theme) =>
                theme.transitions.create("transform", {
                  duration: theme.transitions.duration.shorter,
                  easing: theme.transitions.easing.easeInOut,
                }),
              zIndex: 1200,
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
                    <IconButton
                      aria-label="Back to Settings"
                      size="small"
                      onClick={() =>
                        navigate("/dashboard/settings/profile-settings")
                      }
                      sx={{
                        p: 0,
                        width: 20,
                        minWidth: "auto",
                      }}
                    >
                      <SvgColor
                        src="/assets/icons/custom/lucide--chevron-right.svg"
                        sx={{ transform: "rotate(180deg)" }}
                        width={16}
                        height={16}
                        color="text.secondary"
                      />
                    </IconButton>
                  </Box>
                  <Typography
                    variant="caption"
                    fontWeight={600}
                    sx={{ mt: 1, px: 1, display: "block" }}
                    noWrap
                  >
                    {workspaceNav.name}
                  </Typography>
                </Box>
                <Box sx={PANEL_LIST_SX}>
                  <NavSectionMini
                    data={workspaceNav.data}
                    slotProps={{
                      currentRole: user?.role,
                      gap: 0,
                    }}
                    sx={{ paddingTop: 1 }}
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
                    <IconButton
                      aria-label="Back to app"
                      size="small"
                      onClick={() => {
                        const appRedirectUrl = localStorage.getItem(
                          "redirect-url-from-settings",
                        );
                        navigate(appRedirectUrl || "/dashboard/develop");
                        setSettingOpen(false);
                        localStorage.removeItem("redirect-url-from-settings");
                      }}
                      sx={{
                        p: 0,
                        width: 20,
                        minWidth: "auto",
                      }}
                    >
                      <SvgColor
                        src="/assets/icons/custom/lucide--chevron-right.svg"
                        sx={{ transform: "rotate(180deg)" }}
                        width={16}
                        height={16}
                        color="text.primary"
                      />
                    </IconButton>
                  </Box>
                </Box>
                <Box sx={PANEL_LIST_SX}>
                  <NavSectionMini
                    data={navSettingsData}
                    slotProps={{
                      currentRole: user?.role,
                      gap: 0,
                    }}
                    sx={{ paddingTop: 1.5 }}
                  />
                </Box>
              </>
            )}
          </Box>
        </Box>
      </Stack>
    </Box>
  );
}
