import { alpha } from "@mui/material/styles";
import { svgIconClasses } from "@mui/material/SvgIcon";
import { autocompleteClasses } from "@mui/material/Autocomplete";

import { paper, menuItem } from "../../css";
import { darkElevatedStyles } from "../dark-elevated";

// ----------------------------------------------------------------------

export function autocomplete(theme) {
  return {
    MuiAutocomplete: {
      styleOverrides: {
        root: {
          // MUI drops the typing space to 0 on hover and focus, so a field full of tags jumps a row; it keeps 30px.
          [`&.${autocompleteClasses.focused} .${autocompleteClasses.input}, &:hover .${autocompleteClasses.input}`]:
            { minWidth: 30 },
          [`& span.${autocompleteClasses.tag}`]: {
            ...theme.typography.subtitle2,
            height: 24,
            minWidth: 24,
            lineHeight: "24px",
            textAlign: "center",
            padding: theme.spacing(0, 0.75),
            color: theme.palette.text.secondary,
            borderRadius: theme.shape.borderRadius,
            backgroundColor: alpha(theme.palette.grey[500], 0.16),
          },
        },
        paper: {
          ...paper({ theme, dropdown: true }),
          ...darkElevatedStyles(theme),
        },
        listbox: {
          padding: 0,
          [`& .${autocompleteClasses.option}`]: {
            ...menuItem(theme),
          },
        },
        endAdornment: {
          [`& .${svgIconClasses.root}`]: {
            width: 18,
            height: 18,
          },
        },
      },
    },
  };
}
