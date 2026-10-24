// ----------------------------------------------------------------------

export function radio(theme) {
  return {
    // CHECKBOX, RADIO, SWITCH
    MuiFormControlLabel: {
      styleOverrides: {
        label: {
          fontSize: 13,
          lineHeight: "20px",
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
