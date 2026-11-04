import { alertClasses } from "@mui/material/Alert";

// ----------------------------------------------------------------------

const COLORS = ["info", "success", "warning", "error"];
const ROLE = {
  info: "info",
  success: "success",
  warning: "warning",
  error: "danger",
};

// Design system banner: status tint fill, matching tint border, icon in the
// status text colour, 13px message in fg.default, radius.lg. Every variant
// (standard / outlined / filled) renders the same calm banner.
export function alert(theme) {
  const rootStyles = (ownerState) => {
    const role = ROLE[ownerState.severity] || "info";
    const s = theme.palette.status[role];
    return {
      padding: "10px 12px",
      borderRadius: 8,
      alignItems: "flex-start",
      color: theme.palette.text.primary,
      backgroundColor: s.bg,
      border: `1px solid ${s.border}`,
      [`& .${alertClasses.icon}`]: {
        padding: 0,
        marginRight: 10,
        marginTop: 2,
        fontSize: 16,
        opacity: 1,
        color: s.fg,
      },
      [`& .${alertClasses.message}`]: {
        padding: 0,
        fontSize: 12,
        lineHeight: "18px",
        fontWeight: 400,
      },
      [`& .${alertClasses.action}`]: {
        paddingTop: 0,
        marginRight: -4,
      },
    };
  };

  return {
    MuiAlert: {
      styleOverrides: {
        root: ({ ownerState }) => rootStyles(ownerState),
        icon: {
          opacity: 1,
        },
      },
    },
    MuiAlertTitle: {
      styleOverrides: {
        root: {
          marginTop: 0,
          marginBottom: 2,
          fontSize: 12,
          lineHeight: "18px",
          fontWeight: 600,
        },
      },
    },
  };
}

export { COLORS };
