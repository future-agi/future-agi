import { alpha } from "@mui/material/styles";
import { linearProgressClasses } from "@mui/material/LinearProgress";

// ----------------------------------------------------------------------

const COLORS = ["primary", "secondary", "info", "success", "warning", "error"];

// ----------------------------------------------------------------------

export function progress(theme) {
  const rootStyles = (ownerState) => {
    const bufferVariant = ownerState.variant === "buffer";

    const defaultStyle = {
      borderRadius: 4,
      [`& .${linearProgressClasses.bar}`]: {
        borderRadius: 4,
      },
      ...(bufferVariant && {
        backgroundColor: "transparent",
      }),
    };

    const colorStyle = COLORS.map((color) => ({
      ...(ownerState.color === color && {
        backgroundColor: alpha(theme.palette[color].main, 0.24),
      }),
    }));

    return [defaultStyle, ...colorStyle];
  };

  return {
    MuiLinearProgress: {
      styleOverrides: {
        root: ({ ownerState }) => rootStyles(ownerState),
      },
    },
    // Design system: a spinner is grey (fg.secondary), the same as the shared
    // Loader, so a page handing over from the route loader to its own spinner
    // reads as one loader. Only spinning (indeterminate) rings in the default
    // colour change: score/progress rings (determinate), color="inherit"
    // spinners inside buttons and status colours keep theirs.
    MuiCircularProgress: {
      styleOverrides: {
        root: ({ ownerState }) =>
          ownerState.variant === "indeterminate" &&
          ownerState.color === "primary"
            ? { color: theme.palette.text.secondary }
            : {},
      },
    },
  };
}
