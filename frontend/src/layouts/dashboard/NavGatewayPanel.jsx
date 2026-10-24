import React from "react";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";

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
      sx={{
        width: gatewayOpen ? PANEL_WIDTH : 0,
        minWidth: gatewayOpen ? PANEL_WIDTH : 0,
        overflow: "hidden",
        transition: (theme) =>
          theme.transitions.create(["width", "min-width"], {
            duration: theme.transitions.duration.shorter,
            easing: theme.transitions.easing.easeInOut,
          }),
        height: "100vh",
        borderRight: (theme) =>
          gatewayOpen ? `solid 1px ${theme.palette.divider}` : "none",
        flexShrink: 0,
      }}
    >
      <Box
        sx={{
          width: PANEL_WIDTH,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          opacity: gatewayOpen ? 1 : 0,
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
              fontSize: 12,
              fontWeight: 500,
              letterSpacing: 0,
              textTransform: "none",
              color: "text.tertiary",
            }}
          >
            Gateway
          </Typography>
          <IconButton
            size="small"
            onClick={() => setGatewayOpen(false)}
            sx={{ width: 20, color: "text.disabled" }}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={16} />
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
                      fontSize: 12,
                      fontWeight: 500,
                      letterSpacing: 0,
                      textTransform: "none",
                      color: "text.tertiary",
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
                        // Same as the main sidebar: grey at rest, neutral fill
                        // and fg.default when current — no purple.
                        color: isActive ? "text.primary" : "text.secondary",
                        bgcolor: isActive ? "action.selected" : "transparent",
                        "&:hover": {
                          bgcolor: isActive ? "action.selected" : "action.hover",
                          color: "text.primary",
                        },
                        transition: "all 0.15s ease",
                      }}
                    >
                      <Box
                        sx={{
                          width: 14,
                          height: 14,
                          flexShrink: 0,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          color: "inherit",
                        }}
                      >
                        {item.iconName ? (
                          <Iconify icon={item.iconName} width={14} />
                        ) : item.icon ? (
                          <Box sx={{ "& svg": { width: 15, height: 15 } }}>
                            {item.icon}
                          </Box>
                        ) : null}
                      </Box>
                      <Typography
                        sx={{
                          fontSize: "13px",
                          fontWeight: isActive ? 500 : 400,
                          whiteSpace: "nowrap",
                          lineHeight: "20px",
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
