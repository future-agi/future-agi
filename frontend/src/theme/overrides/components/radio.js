// ----------------------------------------------------------------------

export function radio(theme) {
  return {
    // CHECKBOX, RADIO, SWITCH
    MuiFormControlLabel: {
      styleOverrides: {
        label: {
          fontSize: 12,
          lineHeight: "18px",
        },
      },
    },

    MuiRadio: {
      styleOverrides: {
        root: {
          padding: theme.spacing(1),
          color: theme.palette.border.strong,
          "& .MuiSvgIcon-root": { fontSize: 18 },
          "&.Mui-checked": {
            color: theme.palette.control.checked,
          },
        },
      },
    },
  };
}
