import { switchClasses } from "@mui/material/Switch";

// ----------------------------------------------------------------------

export function switches(theme) {
  const lightMode = theme.palette.mode === "light";

  const rootStyles = (ownerState) => ({
    padding: "9px 13px 9px 12px",
    width: 58,
    height: 38,
    ...(ownerState.size === "small" && {
      padding: "4px 8px 4px 7px",
      width: 40,
      height: 24,
    }),
    [`& .${switchClasses.thumb}`]: {
      width: 14,
      height: 14,
      boxShadow: "none",
      color: theme.palette.control.thumb,
      ...(ownerState.size === "small" && {
        width: 10,
        height: 10,
      }),
    },
    [`& .${switchClasses.track}`]: {
      opacity: 1,
      borderRadius: 14,
      backgroundColor: theme.palette.control.track,
    },
    [`& .${switchClasses.switchBase}`]: {
      left: 3,
      padding: 12,
      ...(ownerState.size === "small" && {
        padding: 7,
      }),
      [`&.${switchClasses.checked}`]: {
        transform: "translateX(13px)",
        [`& .${switchClasses.thumb}`]: {
          color: theme.palette.control.onChecked,
        },
        [`&+.${switchClasses.track}`]: {
          opacity: 1,
          backgroundColor: theme.palette.control.checked,
        },
        ...(ownerState.size === "small" && {
          transform: "translateX(9px)",
        }),
      },
      [`&.${switchClasses.disabled}`]: {
        [`& .${switchClasses.thumb}`]: {
          opacity: lightMode ? 1 : 0.48,
        },
        [`&+.${switchClasses.track}`]: {
          opacity: 0.48,
        },
      },
    },
  });

  return {
    MuiSwitch: {
      styleOverrides: {
        root: ({ ownerState }) => rootStyles(ownerState),
      },
    },
  };
}
