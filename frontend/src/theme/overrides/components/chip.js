import { alpha } from "@mui/material/styles";
import { chipClasses } from "@mui/material/Chip";

// ----------------------------------------------------------------------

const COLORS = ["primary", "secondary", "info", "success", "warning", "error"];

// Future AGI Design System badges and tags: 20px (small) / 24px (medium),
// 12px at 500, radius.sm. Status colours are a tint with darker text, never a
// solid fill or a coloured outline. Purple is kept for the main action, so
// primary/secondary chips render neutral.
const STATUS = {
  info: "info",
  success: "success",
  warning: "warning",
  error: "danger",
};

// ----------------------------------------------------------------------

// Themes built without the design-system palette (tests, isolated previews)
// fall back to MUI's own status colours.
function statusPalette(theme) {
  if (theme.palette.status) return theme.palette.status;
  const pick = (c) => ({ fg: c.dark, bg: alpha(c.main, 0.12), solid: c.main });
  return {
    info: pick(theme.palette.info),
    success: pick(theme.palette.success),
    warning: pick(theme.palette.warning),
    danger: pick(theme.palette.error),
    neutral: {
      fg: theme.palette.text.secondary,
      bg: theme.palette.action.selected,
      solid: theme.palette.grey[500],
    },
  };
}

export function chip(theme) {
  const palette = statusPalette(theme);
  const borderDefault = theme.palette.border?.default || theme.palette.divider;
  const muted = theme.palette.background.muted || theme.palette.action.selected;

  const rootStyles = (ownerState) => {
    const outlinedVariant = ownerState.variant === "outlined";
    const role = STATUS[ownerState.color];
    const status = role ? palette[role] : palette.neutral;
    const isNeutral = !role;

    const tone = {
      color: isNeutral ? theme.palette.text.secondary : status.fg,
      backgroundColor: outlinedVariant && isNeutral ? "transparent" : status.bg,
      border:
        outlinedVariant && isNeutral
          ? `1px solid ${borderDefault}`
          : "1px solid transparent",
      ...(ownerState.clickable && {
        "&:hover": {
          backgroundColor: isNeutral
            ? theme.palette.action.selected
            : alpha(status.solid, 0.18),
        },
      }),
      [`& .${chipClasses.icon}`]: {
        color: isNeutral ? theme.palette.action.active : status.fg,
      },
      [`& .${chipClasses.avatar}`]: {
        color: theme.palette.text.secondary,
        backgroundColor: muted,
      },
    };

    const size =
      ownerState.size === "small"
        ? {
            height: 20,
            [`& .${chipClasses.label}`]: { paddingLeft: 6, paddingRight: 6 },
          }
        : {
            height: 24,
            [`& .${chipClasses.label}`]: { paddingLeft: 8, paddingRight: 8 },
          };

    return [
      {
        fontSize: 12,
        lineHeight: "16px",
        fontWeight: 500,
        borderRadius: 4,
        [`& .${chipClasses.icon}`]: {
          width: 12,
          height: 12,
          marginLeft: 6,
          marginRight: -2,
        },
        [`& .${chipClasses.deleteIcon}`]: {
          width: 14,
          height: 14,
          marginRight: 4,
          opacity: 0.6,
          color: "currentColor",
          "&:hover": { opacity: 1, color: "currentColor" },
        },
      },
      tone,
      size,
      {
        [`&.${chipClasses.disabled}`]: {
          opacity: 1,
          color: theme.palette.text.disabled,
          backgroundColor: theme.palette.action.disabledBackground,
          borderColor: "transparent",
        },
      },
    ];
  };

  return {
    MuiChip: {
      styleOverrides: {
        root: ({ ownerState }) => rootStyles(ownerState),
      },
    },
  };
}

export { COLORS };
