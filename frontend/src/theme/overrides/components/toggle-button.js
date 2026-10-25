import { toggleButtonClasses } from "@mui/material/ToggleButton";
import { toggleButtonGroupClasses } from "@mui/material/ToggleButtonGroup";

// ----------------------------------------------------------------------
// Design system segmented control (.ds-seg). A ToggleButtonGroup renders the
// same control as SegmentedTabs (components/tabs): a bg.muted track with 2px
// padding, 24px options in 12px/500, the selected option a surface pill with
// a hairline ring. Callers don't restyle it.
// ----------------------------------------------------------------------

export function toggleButton(theme) {
  const ring = theme.palette.border?.default || theme.palette.divider;

  return {
    MuiToggleButtonGroup: {
      styleOverrides: {
        root: ({ ownerState }) => ({
          gap: 2,
          padding: 2,
          borderRadius: 6,
          border: 0,
          backgroundColor: theme.palette.background.muted,
          width: ownerState.fullWidth ? "100%" : "fit-content",
        }),
        grouped: ({ ownerState }) => ({
          // A full-width group splits the track evenly.
          ...(ownerState.fullWidth && { flex: "1 1 0", minWidth: 0 }),
          margin: 0,
          border: 0,
          borderRadius: 4,
          [`&.${toggleButtonGroupClasses.middleButton}, &.${toggleButtonGroupClasses.lastButton}`]: {
            marginLeft: 0,
            borderLeft: 0,
            borderRadius: 4,
          },
          [`&.${toggleButtonGroupClasses.firstButton}`]: { borderRadius: 4 },
        }),
      },
    },
    MuiToggleButton: {
      styleOverrides: {
        root: {
          height: 24,
          padding: "0 10px",
          // Options never wrap.
          whiteSpace: "nowrap",
          border: 0,
          borderRadius: 4,
          textTransform: "none",
          fontSize: 12,
          lineHeight: "24px",
          fontWeight: 500,
          color: theme.palette.text.secondary,
          "&:hover": {
            color: theme.palette.text.primary,
            backgroundColor: "transparent",
          },
          [`&.${toggleButtonClasses.selected}`]: {
            color: theme.palette.text.primary,
            backgroundColor: theme.palette.background.paper,
            boxShadow: `0 0 0 1px ${ring}`,
            "&:hover": { backgroundColor: theme.palette.background.paper },
          },
          [`&.${toggleButtonClasses.disabled}`]: {
            color: theme.palette.text.disabled,
          },
        },
      },
    },
  };
}
