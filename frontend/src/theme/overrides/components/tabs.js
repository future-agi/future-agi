import { tabClasses } from "@mui/material/Tab";

// ----------------------------------------------------------------------

export function tabs(theme) {
  return {
    MuiTabs: {
      styleOverrides: {
        // Design system tabs: 32px underline tabs, 13px/500, accent underline.
        root: { minHeight: 32 },
        indicator: {
          height: 2,
          borderRadius: 1,
          backgroundColor: theme.palette.secondary.main,
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
          fontSize: 13,
          lineHeight: "20px",
          fontWeight: 500,
          textTransform: "none",
          "&:not(:last-of-type)": {
            marginRight: theme.spacing(1),
          },
          [`&.${tabClasses.selected}`]: {
            color: theme.palette.text.primary,
          },
          [`&:not(.${tabClasses.selected})`]: {
            color: theme.palette.text.subtitle,
            "&:hover": { color: theme.palette.text.primary },
          },
        },
      },
    },
  };
}
