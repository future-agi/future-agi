import React from "react";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import { alpha } from "@mui/material/styles";

import { usePathname } from "src/routes/hooks";
import { useNavigate } from "react-router";
import Scrollbar from "src/components/scrollbar";
import Iconify from "src/components/iconify";

import { useNavGatewayData } from "./config-navigation";
import { useGatewayOpen } from "./states";

const PANEL_WIDTH = 200;

export default function NavGatewayPanel() {
  const pathname = usePathname();
  const navigate = useNavigate();
  const navGatewayData = useNavGatewayData();
  const { gatewayOpen, setGatewayOpen } = useGatewayOpen();
  const isGatewayRoute = pathname.startsWith("/dashboard/gateway");
  const effectiveOpen = gatewayOpen && isGatewayRoute;

  // Collect all items across groups for active path detection
  const allItems = navGatewayData.flatMap((group) => group.items || []);

  // Find the longest matching path to avoid Overview always being active
  const activePath = allItems.reduce((best, item) => {
    const matches =
      pathname === item.path || pathname.startsWith(item.path + "/");
    if (matches && item.path.length > (best?.length || 0)) {
      return item.path;
    }
    return best;
  }, null);

  return (
    <Box
      aria-hidden={!effectiveOpen}
      data-testid="nav-gateway-panel"
      sx={{
        width: effectiveOpen ? PANEL_WIDTH : 0,
        minWidth: effectiveOpen ? PANEL_WIDTH : 0,
        overflow: "hidden",
        transition: (theme) =>
          theme.transitions.create(["width", "min-width"], {
            duration: theme.transitions.duration.shorter,
            easing: theme.transitions.easing.easeInOut,
          }),
        height: "100vh",
        borderRight: (theme) =>
          effectiveOpen ? `solid 1px ${theme.palette.divider}` : "none",
        bgcolor: (theme) =>
          theme.palette.mode === "dark"
            ? alpha(theme.palette.primary.main, 0.04)
            : alpha(theme.palette.primary.main, 0.02),
        flexShrink: 0,
      }}
    >
      <Box
        sx={{
          width: PANEL_WIDTH,
          height: "100%",
          display: effectiveOpen ? "flex" : "none",
          flexDirection: "column",
          opacity: effectiveOpen ? 1 : 0,
          pointerEvents: effectiveOpen ? "auto" : "none",
          transition: (theme) =>
            theme.transitions.create("opacity", {
              duration: 150,
              easing: theme.transitions.easing.easeInOut,
            }),
        }}
      >
        {/* Header */}
        <Box
          sx={{
            px: 1.5,
            pt: 2,
            pb: 0.5,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Typography
            sx={{
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: 0.8,
              textTransform: "uppercase",
              color: "text.disabled",
            }}
          >
            Gateway
          </Typography>
          <IconButton
            size="small"
            onClick={() => setGatewayOpen(false)}
            sx={{ width: 20, height: 20, color: "text.disabled" }}
          >
            <Iconify icon="mdi:chevron-left" width={16} />
          </IconButton>
        </Box>

        {/* Items */}
        <Scrollbar sx={{ flex: 1 }}>
          <Stack sx={{ px: 0.75, gap: "1px", pb: 2 }}>
            {navGatewayData.map((group, groupIdx) => (
              <React.Fragment key={group.subheader || groupIdx}>
                {/* Group subheader */}
                {group.subheader && (
                  <Typography
                    sx={{
                      fontSize: 9,
                      fontWeight: 700,
                      letterSpacing: 0.8,
                      textTransform: "uppercase",
                      color: "text.disabled",
                      px: 1,
                      pt: groupIdx === 0 ? 0.5 : 1.5,
                      pb: 0.25,
                    }}
                  >
                    {group.subheader}
                  </Typography>
                )}

                {/* Group items */}
                {group.items.map((item) => {
                  const isActive = item.path === activePath;

                  return (
                    <Box
                      key={item.title}
                      onClick={() => navigate(item.path)}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 0.75,
                        px: 1,
                        py: "5px",
                        borderRadius: "6px",
                        cursor: "pointer",
                        color: isActive ? "primary.main" : "text.primary",
                        bgcolor: isActive
                          ? (theme) => alpha(theme.palette.primary.main, 0.08)
                          : "transparent",
                        "&:hover": {
                          bgcolor: (theme) =>
                            isActive
                              ? alpha(theme.palette.primary.main, 0.12)
                              : theme.palette.action.hover,
                        },
                        transition: "all 0.15s ease",
                      }}
                    >
                      <Box
                        sx={{
                          width: 18,
                          height: 18,
                          flexShrink: 0,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          color: isActive ? "primary.main" : "text.primary",
                        }}
                      >
                        {item.iconName ? (
                          <Iconify icon={item.iconName} width={15} />
                        ) : item.icon ? (
                          <Box sx={{ "& svg": { width: 15, height: 15 } }}>
                            {item.icon}
                          </Box>
                        ) : null}
                      </Box>
                      <Typography
                        sx={{
                          fontSize: "12px",
                          fontWeight: isActive ? 600 : 500,
                          whiteSpace: "nowrap",
                          lineHeight: 1.2,
                        }}
                      >
                        {item.title}
                      </Typography>
                    </Box>
                  );
                })}
              </React.Fragment>
            ))}
          </Stack>
        </Scrollbar>
      </Box>
    </Box>
  );
}
