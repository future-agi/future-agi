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
    // Design system: a spinner is grey (palette.loader, fg.secondary), the
    // same as the shared Loader, so a page handing over from the route loader
    // to its own spinner reads as one loader. Only the default colour changes:
    // an explicit color="primary", "inherit" or a status colour keeps its own,
    // and score/progress rings (determinate) keep the brand colour.
    MuiCircularProgress: {
      defaultProps: { color: "loader" },
      styleOverrides: {
        root: ({ ownerState }) =>
          ownerState.color === "loader" && ownerState.variant === "determinate"
            ? { color: theme.palette.primary.main }
            : {},
      },
    },
  };
}
