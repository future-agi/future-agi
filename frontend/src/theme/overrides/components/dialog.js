// Design system dialog: bg.overlay, radius.xl, modal shadow (ring + soft drop).
export function dialog(theme) {
  return {
    MuiDialog: {
      styleOverrides: {
        paper: ({ ownerState }) => ({
          boxShadow: theme.customShadows.dialog,
          borderRadius: 12,
          backgroundImage: "none",
          backgroundColor: theme.palette.background.overlay,
          ...(!ownerState.fullScreen && {
            margin: theme.spacing(2),
          }),
        }),
        paperFullScreen: {
          borderRadius: 0,
        },
      },
    },
    MuiDialogTitle: {
      styleOverrides: {
        root: {
          padding: theme.spacing(2, 2.5, 1),
          fontSize: 16,
          lineHeight: "24px",
          fontWeight: 600,
        },
      },
    },
    MuiDialogContent: {
      styleOverrides: {
        root: {
          padding: theme.spacing(1, 2.5),
          fontSize: 14,
          lineHeight: "22px",
        },
        dividers: {
          borderTop: 0,
          borderBottom: `1px solid ${theme.palette.divider}`,
          paddingBottom: theme.spacing(2),
        },
      },
    },
    MuiDialogActions: {
      styleOverrides: {
        root: {
          padding: theme.spacing(1.5, 2.5),
          "& > :not(:first-of-type)": {
            marginLeft: theme.spacing(1),
          },
        },
      },
    },
  };
}
