import { tabClasses } from "@mui/material/Tab";

// ----------------------------------------------------------------------

export function tabs(theme) {
  return {
    MuiTabs: {
      styleOverrides: {
        // Design system tabs: 32px underline tabs, 12px/500, accent underline.
        // A full-width hairline under every horizontal tab row (the tab rail).
        // Drawn as an inset shadow so the 2px indicator sits on top of it.
        // Screens that draw their own line opt out with boxShadow: "none".
        root: ({ ownerState }) => ({
          minHeight: 32,
          ...(ownerState.orientation !== "vertical" && {
            boxShadow: `inset 0 -1px 0 ${theme.palette.divider}`,
          }),
        }),
        indicator: {
          height: 2,
          borderRadius: 1,
          // border.selected: purple in light, white in dark.
          backgroundColor:
            theme.palette.border?.selected || theme.palette.secondary.main,
        },
        scrollButtons: {
          width: 48,
          borderRadius: "50%",
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          padding: "0 6px",
          opacity: 1,
          minWidth: 0,
          minHeight: 32,
          fontSize: 12,
          lineHeight: "18px",
          fontWeight: 500,
          textTransform: "none",
          "&:not(:last-of-type)": {
            marginRight: theme.spacing(1),
          },
          [`&.${tabClasses.selected}`]: {
            color: theme.palette.text.primary,
          },
          // Unselected tabs read as labels, not hints: secondary, not tertiary.
          [`&:not(.${tabClasses.selected})`]: {
            color: theme.palette.text.secondary,
            "&:hover": { color: theme.palette.text.primary },
          },
          // Design system: a tab's icon is 16px, whatever size the screen
          // passes to Iconify or SvgColor.
          [`& .${tabClasses.iconWrapper}`]: {
            width: 16,
            height: 16,
            fontSize: 16,
            flexShrink: 0,
          },
        },
      },
    },
  };
}
