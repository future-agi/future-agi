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
        },
        arrow: {
          color: theme.palette.background.inverse,
        },
      },
    },
  };
}
