import { drawerClasses } from "@mui/material/Drawer";

// Design system drawer: bg.overlay, a 1px border on the open edge, a soft edge
// shadow (customShadows.drawer) instead of the old 80–100px blur.
export function drawer(theme) {
  return {
    MuiDrawer: {
      styleOverrides: {
        root: ({ ownerState }) => ({
          ...(ownerState.variant === "temporary" && {
            [`& .${drawerClasses.paper}`]: {
              backgroundImage: "none",
              backgroundColor: theme.palette.background.overlay,
              ...(ownerState.anchor === "left" && {
                borderRight: `1px solid ${theme.palette.border.default}`,
                boxShadow: theme.customShadows.drawer
                  .replace("-12px", "12px")
                  .replace("-8px 0", "8px 0"),
              }),
              ...(ownerState.anchor === "right" && {
                borderLeft: `1px solid ${theme.palette.border.default}`,
                boxShadow: theme.customShadows.drawer,
              }),
            },
          }),
        }),
      },
    },
  };
}
