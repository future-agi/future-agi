// Design system panel: flat surface, 1px border.default, radius.lg, no shadow.
export function card(theme) {
  return {
    MuiCard: {
      styleOverrides: {
        root: {
          position: "relative",
          boxShadow: "none",
          border: `1px solid ${theme.palette.border.default}`,
          borderRadius: 8,
          backgroundColor: theme.palette.background.paper,
          zIndex: 0, // Fix Safari overflow: hidden with border radius
        },
      },
    },
    MuiCardHeader: {
      styleOverrides: {
        root: {
          padding: theme.spacing(2, 2, 0),
        },
        title: { fontSize: 14, lineHeight: "20px", fontWeight: 600 },
        subheader: {
          fontSize: 12,
          lineHeight: "16px",
          color: theme.palette.text.subtitle,
        },
      },
    },
    MuiCardContent: {
      styleOverrides: {
        root: {
          padding: theme.spacing(2),
          "&:last-child": { paddingBottom: theme.spacing(2) },
        },
      },
    },
  };
}
