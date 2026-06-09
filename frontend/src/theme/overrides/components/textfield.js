import { alpha } from "@mui/material/styles";
import { inputBaseClasses } from "@mui/material/InputBase";
import { inputLabelClasses } from "@mui/material/InputLabel";
import { filledInputClasses } from "@mui/material/FilledInput";
import { outlinedInputClasses } from "@mui/material/OutlinedInput";

// ----------------------------------------------------------------------

export function textField(theme) {
  const lightMode = theme.palette.mode === "light";

  const color = {
    focused: lightMode ? theme.palette.black[500] : theme.palette.grey[400],
    active: lightMode ? theme.palette.black[500] : theme.palette.grey[400],
    placeholder: lightMode ? theme.palette.black[300] : theme.palette.grey[600],
  };

  const font = {
    label: theme.typography.body1,
    value: theme.typography.body2,
  };

  return {
    // HELPER
    MuiFormHelperText: {
      styleOverrides: {
        root: {
          marginTop: theme.spacing(1),
          color: theme.palette.text.secondary,
        },
      },
    },

    // LABEL
    MuiFormLabel: {
      styleOverrides: {
        asterisk: {
          color: theme.palette.red[500],
        },
        root: {
          ...font.value,
          color: color.placeholder,
          [`&.${inputLabelClasses.shrink}`]: {
            ...font.label,
            fontWeight: 600,
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
          "&::placeholder": {
            opacity: 0.5,
            color: color.placeholder,
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
          [`&.${outlinedInputClasses.focused}`]: {
            [`& .${outlinedInputClasses.notchedOutline}`]: {
              borderColor: lightMode
                ? theme.palette.black[100]
                : theme.palette.border.hover,
              borderWidth: 1,
            },
          },
          [`&:hover`]: {
            [`& .${outlinedInputClasses.notchedOutline}`]: {
              borderColor: lightMode
                ? theme.palette.black[100]
                : theme.palette.border.hover,
            },
          },
          [`&.${outlinedInputClasses.error}`]: {
            [`& .${outlinedInputClasses.notchedOutline}`]: {
              borderColor: theme.palette.error.main,
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
          "::placeholder": {
            color: color.placeholder,
            opacity: 1,
          },
        },
        notchedOutline: {
          borderColor: lightMode
            ? theme.palette.black[100]
            : theme.palette.border.default,
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
          paddingLeft: theme.spacing(0.75), // 2px
          paddingRight: theme.spacing(0.75), // 2px
          flexDirection: "row",
          alignItems: "center",
          background: theme.palette.background.paper,
          zIndex: 1, // Ensures label appears above border
        },
      },
    },
  };
}
