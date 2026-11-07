// ----------------------------------------------------------------------

export function select(theme) {
  return {
    MuiSelect: {
      styleOverrides: {
        icon: {
          right: 10,
          width: 18,
          height: 18,
          top: "calc(50% - 9px)",
        },
      },
    },
    MuiFormControl: {
      styleOverrides: {
        root: {
          ".MuiFormLabel-root": {
            "&.MuiInputLabel-shrink.Mui-focused": {
              backgroundColor: theme.palette.background.paper,
              padding: "0 5px",
              borderColor:
                theme.palette.mode === "light"
                  ? theme.palette.black[500]
                  : theme.palette.grey[500],
            },
          },
        },
      },
    },
    MuiNativeSelect: {
      styleOverrides: {
        icon: {
          right: 10,
          width: 18,
          height: 18,
          top: "calc(50% - 9px)",
        },
      },
    },
  };
}
