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
  useNavData,
  useNavSettingsData,
  useNavUpgradeData,
  useWorkspaceSettingsNav,
} from "./config-navigation";
import { IconButton, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import Logo from "src/components/logo";
import { useSettingsContext } from "src/components/settings";
import WorkspaceSwitcher from "./WorkspaceSwitcher/WorkspaceSwitcher";
import { useSettingsOpen } from "./states";
import { useNavigate } from "react-router";

// ----------------------------------------------------------------------

export default function NavMini() {
  const { user } = useAuthContext();
  const settings = useSettingsContext();
  const navData = useNavData();
  const navUpgradeData = useNavUpgradeData();

  const navDashboardData = useNavDashBoardData();

  const navSettingsData = useNavSettingsData();
  const { settingOpen, setSettingOpen } = useSettingsOpen();
  const navigate = useNavigate();
  const pathname = usePathname();

  // Detect workspace-specific settings route
  const wsMatch = pathname.match(
    /\/dashboard\/settings\/workspace\/([0-9a-f-]{36})/,
  );
  const activeWorkspaceId = wsMatch ? wsMatch[1] : null;
  const workspaceNav = useWorkspaceSettingsNav(activeWorkspaceId);

  // Sync settings sidebar visibility with URL on page reload
  useEffect(() => {
    if (pathname.includes("/settings/")) {
      setSettingOpen(true);
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
        height: "100vh",
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
        <Stack
          sx={{ pt: "8px", width: 1 }}
          gap="4px"
          alignItems="center"
        >
          <Logo collapsed />
          <IconButton
            onClick={() =>
              settings.onUpdate(
                "themeLayout",
                settings.themeLayout === "vertical" ? "mini" : "vertical",
              )
            }
            sx={{
              color: "text.secondary",
              width: 36,
              height: 24,
              p: 0,
              borderRadius: "6px",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <SvgColor
              sx={{ height: "14px", width: "14px" }}
              src="/assets/icons/navbar/ic_nav_close_toggle.svg"
            />
          </IconButton>
          <WorkspaceSwitcher collapsed />
          <NavSectionMini
            data={navDashboardData}
            slotProps={{
              currentRole: user?.role,
              gap: 0,
            }}
          />
        </Stack>

        <NavSectionMini
          data={navData}
          slotProps={{
            currentRole: user?.role,
            gap: 0,
          }}
        />
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
            <Box sx={{ flex: 1, overflow: "hidden" }}>
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
            <Box sx={{ flex: 1, overflow: "hidden" }}>
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
  );
}
