// ----------------------------------------------------------------------

export function checkbox(theme) {
  const lightMode = theme.palette.mode === "light";

  return {
    MuiCheckbox: {
      styleOverrides: {
        root: {
          padding: theme.spacing(1),
          // Design system: unchecked border.strong; checked control.checked
          // (purple in light, white in dark, like the main button).
          color: theme.palette.border.strong,
          "& .MuiSvgIcon-root": { fontSize: 18 },

          "&.Mui-checked, &.MuiCheckbox-indeterminate": {
            color: theme.palette.control.checked,
            ...(!lightMode && {
              "& path[stroke]": { stroke: theme.palette.background.paper },
            }),
          },
        },
      },
    },
  };
}
