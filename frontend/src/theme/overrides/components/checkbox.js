// ----------------------------------------------------------------------

export function checkbox(theme) {
  const lightMode = theme.palette.mode === "light";

  return {
    MuiCheckbox: {
      styleOverrides: {
        root: {
          padding: theme.spacing(1),
          color: lightMode
            ? theme.palette.black?.o20
            : theme.palette.grey?.[600],
          "&.Mui-checked": {
            color: theme.palette.purple?.[300],
            ...(!lightMode && {
              "& path[stroke]": { stroke: theme.palette.background.paper },
            }),
          },
        },
      },
    },
  };
}
