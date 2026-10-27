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
import { Divider, IconButton, Typography } from "@mui/material";
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
        backgroundColor: "background.paper",
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
        <Box sx={{ pt: "8px", pb: "8px" }}>
          <Stack
            display={"flex"}
            flexDirection={"column"}
            gap={"8px"}
            justifyContent={"center"}
            alignItems={"center"}
          >
            {/* Design system: the logo mark on top, then expand. */}
            <Logo collapsed />
            <IconButton
              size="small"
              onClick={() =>
                settings.onUpdate(
                  "themeLayout",
                  settings.themeLayout === "vertical" ? "mini" : "vertical",
                )
              }
              sx={{ color: "text.primary" }}
            >
              <SvgColor
                sx={{ height: "16px", width: "16px" }}
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
          <Divider
            orientation="horizontal"
            sx={{
              width: "28px",
              height: "1px",
              mt: "8px",
              backgroundColor: "divider",
              opacity: 1,
              mb: "8px",
              mx: "auto",
            }}
          />
        </Box>

        <NavSectionMini
          data={navData}
          slotProps={{
            currentRole: user?.role,
            gap: 0,
          }}
        />
        <Box sx={{ flexGrow: 1 }} />

        <Stack marginTop={3} direction={"column"} alignItems={"center"}>
          <Divider
            orientation="horizontal"
            sx={{
              width: "28px",
              height: "1px",
              backgroundColor: "divider",
              opacity: 1,
              mb: "8px",
            }}
          />
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
          bgcolor: "background.paper",
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
