import { alpha } from "@mui/material/styles";
import { dividerClasses } from "@mui/material/Divider";
import { checkboxClasses } from "@mui/material/Checkbox";
import { menuItemClasses } from "@mui/material/MenuItem";
import { autocompleteClasses } from "@mui/material/Autocomplete";

// ----------------------------------------------------------------------

// Floating surface (design system bg.overlay). No blur or decorative gradients:
// menus and popovers are solid, ringed and lightly shadowed.
export const paper = ({ theme, bgcolor, dropdown }) => ({
  backgroundColor:
    bgcolor ||
    theme.palette.background.overlay ||
    theme.palette.background.paper,
  ...(dropdown && {
    padding: 4,
    boxShadow: theme.customShadows.dropdown,
    borderRadius: 8,
  }),
});

// ----------------------------------------------------------------------

export const menuItem = (theme) => ({
  fontSize: 13,
  lineHeight: "20px",
  minHeight: 30,
  padding: "5px 8px",
  borderRadius: 4,
  "&:not(:last-of-type)": {
    marginBottom: 1,
  },
  "&:hover": {
    backgroundColor: theme.palette.action.hover,
  },
  [`&.${menuItemClasses.selected}`]: {
    fontWeight: 500,
    backgroundColor: theme.palette.action.selected,
    "&:hover": {
      backgroundColor: theme.palette.action.hover,
    },
  },
  [`& .${checkboxClasses.root}`]: {
    padding: theme.spacing(0.5),
    marginLeft: theme.spacing(-0.5),
    marginRight: theme.spacing(0.5),
  },
  [`&.${autocompleteClasses.option}[aria-selected="true"]`]: {
    backgroundColor: theme.palette.action.selected,
    "&:hover": {
      backgroundColor: theme.palette.action.hover,
    },
  },
  [`&+.${dividerClasses.root}`]: {
    margin: theme.spacing(0.5, 0),
  },
});

// ----------------------------------------------------------------------

export function bgBlur(props) {
  const color = props?.color || "#000000";
  const blur = props?.blur || 6;
  const opacity = props?.opacity || 0.8;
  const imgUrl = props?.imgUrl;

  if (imgUrl) {
    return {
      position: "relative",
      backgroundImage: `url(${imgUrl})`,
      "&:before": {
        position: "absolute",
        top: 0,
        left: 0,
        zIndex: 9,
        content: '""',
        width: "100%",
        height: "100%",
        backdropFilter: `blur(${blur}px)`,
        WebkitBackdropFilter: `blur(${blur}px)`,
        backgroundColor: alpha(color, opacity),
      },
    };
  }

  return {
    backdropFilter: `blur(${blur}px)`,
    WebkitBackdropFilter: `blur(${blur}px)`,
    backgroundColor: alpha(color, opacity),
  };
}

// ----------------------------------------------------------------------

export function bgGradient(props) {
  const direction = props?.direction || "to bottom";
  const startColor = props?.startColor;
  const endColor = props?.endColor;
  const imgUrl = props?.imgUrl;
  const color = props?.color;

  if (imgUrl) {
    return {
      background: `linear-gradient(${direction}, ${startColor || color}, ${
        endColor || color
      }), url(${imgUrl})`,
      backgroundSize: "cover",
      backgroundRepeat: "no-repeat",
      backgroundPosition: "center center",
    };
  }

  return {
    background: `linear-gradient(${direction}, ${startColor}, ${endColor})`,
  };
}

// ----------------------------------------------------------------------

export function textGradient(value) {
  return {
    backgroundImage: `linear-gradient(${value})`,
    backgroundClip: "text",
    backgroundSize: "100%",
    backgroundRepeat: "repeat",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
  };
}

// ----------------------------------------------------------------------

export const hideScroll = {
  x: {
    msOverflowStyle: "none",
    scrollbarWidth: "none",
    overflowX: "scroll",
    "&::-webkit-scrollbar": {
      display: "none",
    },
  },
  y: {
    msOverflowStyle: "none",
    scrollbarWidth: "none",
    overflowY: "scroll",
    "&::-webkit-scrollbar": {
      display: "none",
    },
  },
};
