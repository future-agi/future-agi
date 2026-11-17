import { MaterialDesignContent } from "notistack";
import { styled, alpha, keyframes } from "@mui/material/styles";

// Shared duration constants (ms)
export const SNACKBAR_AUTO_HIDE_MS = 6000;
const EXIT_TRANSITION_MS = 200;
// CSS animation covers autoHide + exit transition so the bar reaches 0% as the toast disappears
const TOTAL_ANIMATION_S = (SNACKBAR_AUTO_HIDE_MS + EXIT_TRANSITION_MS) / 1000;

// Progress bar animation — shrinks from full width to 0 over the toast lifetime
const shrink = keyframes`
  from { width: 100%; }
  to { width: 0%; }
`;

// ----------------------------------------------------------------------

export const StyledNotistack = styled(MaterialDesignContent)(({ theme }) => {
  const isLight = theme.palette.mode === "light";

  return {
    "&.notistack-MuiContent": {
      position: "relative",
      overflow: "hidden",
      color: theme.palette.text.primary,
      backgroundColor: theme.palette.background.paper,
      border: `1px solid ${theme.palette.border.default}`,
      borderRadius: 10,
      boxShadow: isLight
        ? `0 4px 12px 0 ${alpha(theme.palette.common.black, 0.08)}`
        : `0 4px 12px 0 ${alpha(theme.palette.common.black, 0.32)}`,
      padding: "12px 14px",
      paddingBottom: 16,
      minWidth: 320,
      maxWidth: 420,
      alignItems: "center",
      gap: 0,
    },
    "& #notistack-snackbar": {
      padding: 0,
      flexGrow: 1,
      fontSize: "0.875rem",
      fontWeight: 500,
      lineHeight: 1.43,
    },

    // Colored bottom progress bar per variant
    "&.notistack-MuiContent::after": {
      content: '""',
      position: "absolute",
      bottom: 0,
      left: 0,
      height: 3,
      borderRadius: "0 2px 2px 0",
      animation: `${shrink} ${TOTAL_ANIMATION_S}s linear forwards`,
    },
    "&.notistack-MuiContent-success::after": {
      backgroundColor: theme.palette.success.main,
    },
    "&.notistack-MuiContent-error::after": {
      backgroundColor: theme.palette.error.main,
    },
    "&.notistack-MuiContent-warning::after": {
      backgroundColor: theme.palette.warning.dark,
    },
    "&.notistack-MuiContent-info::after": {
      backgroundColor: theme.palette.info.main,
    },
    "&.notistack-MuiContent-default::after": {
      backgroundColor: theme.palette.text.disabled,
    },

    // Pause progress bar on hover (notistack pauses autoHideDuration on hover)
    "&.notistack-MuiContent:hover::after": {
      animationPlayState: "paused",
    },
  };
});

// Inline icon — no background box, just a colored icon with a subtle tint circle
export const StyledIcon = styled("span")(({ color, theme }) => ({
  width: 28,
  height: 28,
  minWidth: 28,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  marginRight: 10,
  borderRadius: "50%",
  color: theme.palette[color]?.icon || theme.palette[color]?.main,
  backgroundColor: alpha(
    theme.palette[color]?.main || theme.palette.grey[500],
    0.12,
  ),
}));
