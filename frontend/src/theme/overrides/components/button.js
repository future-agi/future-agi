import { alpha } from "@mui/material/styles";
import { buttonClasses } from "@mui/material/Button";

// ----------------------------------------------------------------------

const COLORS = ["primary", "secondary", "info", "success", "warning", "error"];

// ----------------------------------------------------------------------

export function button(theme) {
  const lightMode = theme.palette.mode === "light";

  const rootStyles = (ownerState) => {
    const inheritColor = ownerState.color === "inherit";

    const containedVariant = ownerState.variant === "contained";

    const outlinedVariant = ownerState.variant === "outlined";

    const textVariant = ownerState.variant === "text";

    const softVariant = ownerState.variant === "soft";

    const smallSize = ownerState.size === "small";

    const mediumSize = ownerState.size === "medium";

    const largeSize = ownerState.size === "large";

    const defaultStyle = {
      borderRadius: theme.shape.borderRadius,
      textTransform: "none",
      minWidth: 0,
      ...(inheritColor && {
        // CONTAINED
        ...(containedVariant && {
          color: lightMode
            ? theme.palette.common.white
            : theme.palette.grey[800],
          backgroundColor: lightMode
            ? theme.palette.grey[800]
            : theme.palette.common.white,
          "&:hover": {
            backgroundColor: lightMode
              ? theme.palette.grey[700]
              : theme.palette.grey[400],
          },
        }),
        // OUTLINED: the DS secondary button, same as every other outlined
        // button — hairline border.default, never the text colour.
        ...(outlinedVariant && {
          borderColor: `${theme.palette.border.default} !important`,
          color: theme.palette.text.primary,
          backgroundColor: theme.palette.background.paper,
          "&:hover": {
            borderColor: `${theme.palette.border.strong} !important`,
            backgroundColor: theme.palette.action.hover,
          },
          "& .MuiButton-startIcon, & .MuiButton-endIcon": {
            color: theme.palette.action.active,
            "& .svg-color": { color: theme.palette.action.active },
          },
          "&.Mui-disabled": {
            borderColor: lightMode
              ? theme.palette.black[50]
              : theme.palette.border.default,
            backgroundColor: lightMode
              ? theme.palette.whiteScale[50]
              : theme.palette.background.neutral,
            color: lightMode
              ? theme.palette.black[100]
              : theme.palette.text.disabled,
            "& .MuiButton-startIcon, & .MuiButton-endIcon": {
              color: lightMode
                ? theme.palette.black[100]
                : theme.palette.text.disabled,
              "& .svg-color, & svg": {
                color: lightMode
                  ? theme.palette.black[100]
                  : theme.palette.text.disabled,
              },
            },
          },
        }),
        // TEXT
        ...(textVariant && {
          "&:hover": {
            backgroundColor: theme.palette.action.hover,
          },
        }),
        // SOFT
        ...(softVariant && {
          color: theme.palette.text.primary,
          backgroundColor: alpha(theme.palette.grey[500], 0.08),
          "&:hover": {
            backgroundColor: alpha(theme.palette.grey[500], 0.24),
          },
        }),
      }),
      // ...(outlinedVariant && {
      //   "&:hover": {
      //     borderColor: "currentColor",
      //     boxShadow: "0 0 0 0.5px currentColor",
      //   },
      // }),
      "& .MuiButton-startIcon, & .MuiButton-endIcon": {
        // height: theme.spacing(2),
        // width: theme.spacing(2),
        marginLeft: 0,
        marginRight: 6,
        "& .svg-color": { height: 16, width: 16 },
        "& svg": { height: 16, width: 16 },
      },
      "&.Mui-disabled": {
        "& .MuiButton-startIcon, & .MuiButton-endIcon": {
          color: theme.palette.action.disabled,
          "& .svg-color, & svg": {
            color: theme.palette.action.disabled,
          },
        },
      },
    };

    // Future AGI Design System: one purple main button per view; outlined and
    // text buttons are neutral (secondary / ghost); no coloured glow on hover.
    const neutral = !["error", "warning", "success", "info"].includes(
      ownerState.color,
    );
    const colorStyle = COLORS.map((color) => ({
      ...(ownerState.color === color && {
        // CONTAINED
        ...(containedVariant && {
          boxShadow: "none",
          "&:hover": {
            boxShadow: "none",
            backgroundColor: theme.palette[color].dark,
          },
        }),
        // OUTLINED: neutral secondary button
        ...(outlinedVariant &&
          neutral && {
            color: theme.palette.text.primary,
            borderColor: theme.palette.border.default,
            backgroundColor: theme.palette.background.paper,
            "&:hover": {
              borderColor: theme.palette.border.strong,
              backgroundColor: theme.palette.action.hover,
            },
            "& .MuiButton-startIcon, & .MuiButton-endIcon": {
              color: theme.palette.action.active,
            },
          }),
        ...(outlinedVariant &&
          !neutral && {
            borderColor:
              theme.palette.status?.[color === "error" ? "danger" : color]
                ?.border,
            "&:hover": { backgroundColor: theme.palette.action.hover },
          }),
        // TEXT: neutral ghost button
        ...(textVariant &&
          neutral && {
            color: theme.palette.text.secondary,
            "&:hover": {
              color: theme.palette.text.primary,
              backgroundColor: theme.palette.action.hover,
            },
          }),
        // SOFT
        ...(softVariant && {
          color: theme.palette[color].dark,
          backgroundColor: theme.palette[color].lighter,
          "&:hover": {
            backgroundColor: alpha(theme.palette[color].main, 0.16),
          },
        }),
      }),
    }));

    const disabledState = {
      [`&.${buttonClasses.disabled}`]: {
        ...((containedVariant || softVariant) && {
          backgroundColor: theme.palette.action.disabledBackground,
          color: theme.palette.text.disabled,
          borderColor: "transparent",
        }),
        ...(outlinedVariant && {
          backgroundColor: theme.palette.action.disabledBackground,
          borderColor: "transparent",
          color: theme.palette.text.disabled,
        }),
      },
    };

    // Sizes: 28 / 32 / 36 (design-system controls), 13px label at 500.
    const size = {
      fontWeight: 500,
      boxShadow: "none",
      ...(smallSize && {
        height: 28,
        fontSize: 11,
        lineHeight: "16px",
        paddingLeft: 10,
        paddingRight: 10,
        paddingTop: 0,
        paddingBottom: 0,
        ...(textVariant && { paddingLeft: 8, paddingRight: 8 }),
      }),
      ...(mediumSize && {
        height: 32,
        fontSize: 12,
        lineHeight: "18px",
        paddingLeft: 12,
        paddingRight: 12,
        paddingTop: 0,
        paddingBottom: 0,
        ...(textVariant && { paddingLeft: 8, paddingRight: 8 }),
      }),
      ...(largeSize && {
        height: 36,
        fontSize: 12,
        lineHeight: "18px",
        paddingLeft: 14,
        paddingRight: 14,
        ...(textVariant && { paddingLeft: 10, paddingRight: 10 }),
      }),
    };

    // Every neutral outlined button (primary, secondary, inherit, default)
    // is the DS secondary button: hairline border, never purple or the text
    // colour. Applied last so it wins over MUI's own outlined colours.
    const outlinedNeutral = outlinedVariant &&
      neutral && {
        color: theme.palette.text.primary,
        borderColor: theme.palette.border.default,
        backgroundColor: theme.palette.background.paper,
        "&:hover": {
          borderColor: theme.palette.border.strong,
          backgroundColor: theme.palette.action.hover,
        },
      };

    return [defaultStyle, ...colorStyle, disabledState, size, outlinedNeutral || {}];
  };

  return {
    // Design system icon button: a 6px-rounded square, not a circle; hover
    // and focus come from the same tokens as ghost buttons.
    MuiIconButton: {
      styleOverrides: {
        root: {
          borderRadius: 6,
          "&:hover": { backgroundColor: theme.palette.action.hover },
          "&.Mui-focusVisible": {
            outline: `2px solid ${theme.palette.border.focus}`,
            outlineOffset: -2,
          },
        },
        sizeSmall: { padding: 4 },
        sizeMedium: { padding: 6 },
      },
    },
    MuiButton: {
      styleOverrides: {
        // Button labels never wrap onto two lines.
        root: ({ ownerState }) => [
          { whiteSpace: "nowrap" },
          ...rootStyles(ownerState),
        ],
      },
    },
  };
}
