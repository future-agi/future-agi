import { dsSize, dsRadius, dsTypography as type } from "../ds-tokens";

// Product adapter for v0.2 component roles. Vel's extracted overrides supply
// behavior/states; these pinned roles replace their older v0.1 density.
export function dsFoundation(theme) {
  const p = theme.palette;
  const focus = { outline: `2px solid ${p.border.focus}`, outlineOffset: 2 };
  return {
    MuiCssBaseline: {
      styleOverrides: {
        body: { ...type.body_sm, fontFamily: type.fontFamily },
        ":focus-visible": focus,
        "@media (prefers-reduced-motion: reduce)": {
          "*, *::before, *::after": {
            animationDuration: "0.01ms !important",
            transitionDuration: "0.01ms !important",
            scrollBehavior: "auto !important",
          },
        },
      },
    },
    MuiTypography: { defaultProps: { variant: "body2" } },
    MuiButton: {
      styleOverrides: {
        root: ({ ownerState }) => ({
          ...type.button,
          height:
            dsSize.control[
              ownerState.size === "small"
                ? "sm"
                : ownerState.size === "large"
                  ? "lg"
                  : "md"
            ],
          "&.Mui-focusVisible": focus,
        }),
      },
    },
    MuiButtonBase: {
      styleOverrides: { root: { "&.Mui-focusVisible": focus } },
    },
    MuiFormControlLabel: { styleOverrides: { label: type.body_sm } },
    MuiFormHelperText: { styleOverrides: { root: type.caption } },
    MuiFormLabel: { styleOverrides: { root: type.label_md } },
    MuiInputBase: {
      styleOverrides: {
        input: {
          ...type.body_sm,
          "&::placeholder": { color: p.text.tertiary, opacity: 1 },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: ({ ownerState }) => ({
          ...(!ownerState.multiline && { minHeight: dsSize.control.md }),
        }),
        input: {
          ...type.body_sm,
          padding: "6px 12px",
          "&::placeholder": { color: p.text.tertiary, opacity: 1 },
        },
        inputSizeSmall: { padding: "6px 10px" },
        inputMultiline: { padding: 0 },
      },
    },
    MuiInputLabel: { styleOverrides: { outlined: { "&&&": type.label_md } } },
    MuiTab: {
      styleOverrides: {
        root: { ...type.label_md, minHeight: dsSize.control.md },
      },
    },
    MuiTabs: { styleOverrides: { root: { minHeight: dsSize.control.md } } },
    MuiToggleButton: { styleOverrides: { root: { ...type.label_md } } },
    MuiChip: {
      styleOverrides: {
        root: ({ ownerState }) => ({
          ...type.label_sm,
          height:
            ownerState.size === "small" ? dsSize.badge.sm : dsSize.badge.md,
        }),
      },
    },
    MuiMenuItem: {
      styleOverrides: {
        root: { ...type.body_sm, minHeight: dsSize.row.compact },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: {
          ...type.body_sm,
          height: dsSize.row.default,
          paddingTop: 6,
          paddingBottom: 7,
        },
        sizeSmall: {
          height: dsSize.row.compact,
          paddingTop: 4,
          paddingBottom: 5,
        },
        head: {
          ...type.label_md,
          height: dsSize.tableHeader.default,
          paddingTop: 0,
          paddingBottom: 0,
        },
        paddingCheckbox: { "& .MuiCheckbox-root": { padding: 4 } },
      },
    },
    MuiDataGrid: {
      defaultProps: {
        rowHeight: dsSize.row.default,
        columnHeaderHeight: dsSize.tableHeader.default,
      },
      styleOverrides: { root: type.body_sm },
    },
    MuiCardHeader: {
      styleOverrides: {
        root: {
          minHeight: dsSize.control.lg,
          padding: "4px 12px",
          borderBottom: `1px solid ${p.border.default}`,
        },
        title: type.title_xs,
        subheader: type.caption,
      },
    },
    MuiCardContent: {
      styleOverrides: {
        root: { padding: 12, "&:last-child": { paddingBottom: 12 } },
      },
    },
    MuiDialogTitle: { styleOverrides: { root: type.title_sm } },
    MuiDialogContent: { styleOverrides: { root: type.body_md } },
    MuiDialog: {
      styleOverrides: {
        paperWidthXs: { maxWidth: dsSize.dialog.sm },
        paperWidthSm: { maxWidth: dsSize.dialog.md },
        paperWidthMd: { maxWidth: dsSize.dialog.lg },
        paperWidthLg: { maxWidth: dsSize.dialog.xl },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: ({ ownerState }) => ({
          ...(ownerState.variant === "temporary" && {
            backgroundColor: p.background.overlay,
          }),
        }),
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          ...type.caption,
          color: p.mode === "dark" ? dsInverseLight(theme) : p.grey[50],
          "&.ds-hovercard": {
            backgroundColor: p.background.overlay,
            borderRadius: dsRadius.lg,
            "& .MuiTooltip-arrow": { color: p.background.overlay },
          },
        },
      },
    },
  };
}

function dsInverseLight(theme) {
  return theme.palette.grey[900];
}
