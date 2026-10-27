import { alpha } from "@mui/material/styles";
import { inputBaseClasses } from "@mui/material/InputBase";
import { inputLabelClasses } from "@mui/material/InputLabel";
import { filledInputClasses } from "@mui/material/FilledInput";
import { outlinedInputClasses } from "@mui/material/OutlinedInput";

// ----------------------------------------------------------------------

export function textField(theme) {
  // Future AGI Design System: 32px fields (small) / 36px (medium), 13px
  // values, border.default at rest, border.strong on hover, border.focus plus a
  // soft ring when focused.
  const color = {
    focused: theme.palette.text.secondary,
    active: theme.palette.text.secondary,
    placeholder: theme.palette.text.disabled,
  };

  const font = {
    label: { fontSize: 12, lineHeight: "18px" },
    value: { fontSize: 12, lineHeight: "18px" },
  };

  return {
    // HELPER
    MuiFormHelperText: {
      styleOverrides: {
        root: {
          marginTop: 6,
          marginLeft: 0,
          fontSize: 11,
          lineHeight: "16px",
          color: theme.palette.text.subtitle,
        },
      },
    },

    // LABEL
    MuiFormLabel: {
      styleOverrides: {
        asterisk: {
          color: theme.palette.error.main,
        },
        root: {
          ...font.value,
          color: color.placeholder,
          [`&.${inputLabelClasses.shrink}`]: {
            ...font.label,
            fontWeight: 500,
            color: color.active,
            [`&.${inputLabelClasses.focused}`]: {
              color: color.focused,
            },
            [`&.${inputLabelClasses.error}`]: {
              color: theme.palette.error.main,
            },
            [`&.${inputLabelClasses.disabled}`]: {
              color: theme.palette.text.disabled,
            },
            [`&.${inputLabelClasses.filled}`]: {
              transform: "translate(12px, 6px) scale(0.75)",
            },
          },
        },
      },
    },

    // BASE
    MuiInputBase: {
      styleOverrides: {
        root: {
          [`&.${inputBaseClasses.disabled}`]: {
            "& svg": {
              color: theme.palette.text.disabled,
            },
          },
        },
        input: {
          ...font.value,
          colorScheme: theme.palette.mode,
          "&::placeholder": {
            opacity: 0.5,
            color: color.placeholder,
          },
          "&:-webkit-autofill, &:-webkit-autofill:hover, &:-webkit-autofill:focus, &:-webkit-autofill:active":
            {
              WebkitBoxShadow: `0 0 0 1000px var(--input-surface, ${theme.palette.background.paper}) inset !important`,
              WebkitTextFillColor: `${theme.palette.text.primary} !important`,
              caretColor: theme.palette.text.primary,
            },
          "&:autofill, &:autofill:hover, &:autofill:focus, &:autofill:active": {
            boxShadow: `0 0 0 1000px var(--input-surface, ${theme.palette.background.paper}) inset`,
            WebkitTextFillColor: `${theme.palette.text.primary} !important`,
            caretColor: theme.palette.text.primary,
          },
        },
      },
    },

    // STANDARD
    MuiInput: {
      styleOverrides: {
        underline: {
          "&:before": {
            borderBottomColor: alpha(theme.palette.grey[500], 0.32),
          },
          "&:after": {
            borderBottomColor: color.focused,
          },
        },
      },
    },

    // OUTLINED
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          borderRadius: theme.shape.borderRadius,
          [`&.${outlinedInputClasses.focused}`]: {
            boxShadow: theme.customShadows.focusInput,
            [`& .${outlinedInputClasses.notchedOutline}`]: {
              borderColor: theme.palette.border.focus,
              borderWidth: 1,
            },
          },
          [`&:hover:not(.${outlinedInputClasses.focused})`]: {
            [`& .${outlinedInputClasses.notchedOutline}`]: {
              borderColor: theme.palette.border.strong,
            },
          },
          [`&.${outlinedInputClasses.error}`]: {
            [`& .${outlinedInputClasses.notchedOutline}`]: {
              borderColor: theme.palette.error.main,
            },
            [`&.${outlinedInputClasses.focused}`]: {
              boxShadow: `0 0 0 3px ${theme.palette.error.lighter}`,
            },
          },
          [`&.${outlinedInputClasses.disabled}`]: {
            [`& .${outlinedInputClasses.notchedOutline}`]: {
              borderColor: theme.palette.action.disabledBackground,
            },
          },
        },
        input: {
          backgroundColor: "transparent",
          color: theme.palette.text.primary,
          padding: "6px 12px",
          "::placeholder": {
            color: color.placeholder,
            opacity: 1,
          },
        },
        inputSizeSmall: {
          padding: "4px 10px",
        },
        // No notch: the label lives above the field, so the border runs
        // unbroken.
        notchedOutline: {
          top: 0,
          "& legend": { display: "none" },
          borderColor: theme.palette.border.default,
          transition: theme.transitions.create(["border-color"], {
            duration: theme.transitions.duration.shortest,
          }),
        },
      },
    },

    // FILLED
    MuiFilledInput: {
      styleOverrides: {
        root: {
          borderRadius: theme.shape.borderRadius,
          backgroundColor: alpha(theme.palette.grey[500], 0.08),
          "&:hover": {
            backgroundColor: alpha(theme.palette.grey[500], 0.16),
          },
          [`&.${filledInputClasses.focused}`]: {
            backgroundColor: alpha(theme.palette.grey[500], 0.16),
          },
          [`&.${filledInputClasses.error}`]: {
            backgroundColor: alpha(theme.palette.error.main, 0.08),
            [`&.${filledInputClasses.focused}`]: {
              backgroundColor: alpha(theme.palette.error.main, 0.16),
            },
          },
          [`&.${filledInputClasses.disabled}`]: {
            backgroundColor: theme.palette.action.disabledBackground,
          },
        },
      },
    },

    // INPUT LABELS
    MuiInputLabel: {
      defaultProps: {
        shrink: true,
      },
      styleOverrides: {
        root: {
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
        },
        // Design system: the label sits above the field (13/20, 500,
        // fg.default, 6px gap), never inside the border. `&&&` outranks the
        // FormLabel shrink/filled transforms above.
        outlined: {
          "&&&": {
            position: "relative",
            transform: "none",
            marginBottom: 6,
            padding: 0,
            maxWidth: "100%",
            fontSize: 12,
            lineHeight: "18px",
            fontWeight: 500,
            color: theme.palette.text.primary,
          },
          [`&&&.${inputLabelClasses.error}`]: {
            color: theme.palette.error.main,
          },
          [`&&&.${inputLabelClasses.disabled}`]: {
            color: theme.palette.text.disabled,
          },
        },
      },
    },
  };
}
