// Design system tooltip: inverse surface, 12px text, radius.sm, max 240px.
export function tooltip(theme) {
  return {
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: theme.palette.background.inverse,
          color: theme.palette.mode === "dark" ? "#18181B" : "#FAFAFA",
          fontSize: 12,
          lineHeight: "16px",
          fontWeight: 400,
          padding: "4px 8px",
          borderRadius: 4,
          maxWidth: 240,
          // Hover card: rich content (breakdowns, previews, small tables) on the
          // overlay surface, so its text tokens read as on any panel. Plain
          // text stays the inverse tooltip above.
          "&.ds-hovercard": {
            backgroundColor: theme.palette.background.paper,
            color: theme.palette.text.primary,
            border: `1px solid ${theme.palette.border?.default || theme.palette.divider}`,
            boxShadow: theme.customShadows?.dropdown,
            borderRadius: 8,
            padding: 12,
            maxWidth: 400,
            "& .MuiTooltip-arrow": {
              color: theme.palette.background.paper,
              "&::before": {
                border: `1px solid ${theme.palette.border?.default || theme.palette.divider}`,
              },
            },
            "& a, & .MuiLink-root": { color: theme.palette.text.primary },
          },
          // Everything inside a plain tooltip takes its text colour, so no
          // hand-set colour can make it unreadable on the inverse surface.
          "&:not(.ds-hovercard) *": { color: "inherit" },
          // Links inside a tooltip use the tooltip's own text colour.
          "& a, & .MuiLink-root": {
            color: "inherit",
            fontWeight: 500,
            textDecoration: "underline",
          },
        },
        arrow: {
          color: theme.palette.background.inverse,
        },
      },
    },
  };
}
