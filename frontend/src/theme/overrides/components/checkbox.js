// ----------------------------------------------------------------------

export function checkbox(theme) {
  const lightMode = theme.palette.mode === "light";

  return {
    MuiCheckbox: {
      styleOverrides: {
        root: {
          padding: theme.spacing(1),
          // Unchecked: the icon role (action.active), so the outline reaches
          // 3:1 on every surface in both themes; border.strong reads at about
          // 1.5:1. Checked: control.checked (purple in light, white in dark,
          // like the main button).
          color: theme.palette.action.active,
          "& .MuiSvgIcon-root": { fontSize: 18 },

          "&.Mui-checked, &.MuiCheckbox-indeterminate": {
            color: theme.palette.control.checked,
            ...(!lightMode && {
              "& path[stroke]": { stroke: theme.palette.control.onChecked },
            }),
          },
        },
      },
    },
  };
}
