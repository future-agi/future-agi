export const QUERY_TABS_SX = {
  minHeight: 40,
  "& .MuiTab-root": {
    minHeight: 40,
    minWidth: 0,
    px: 1.25,
    typography: "s2",
    fontWeight: "fontWeightSemiBold",
    textTransform: "none",
    "&:not(:last-of-type)": { mr: 0 },
  },
  "& .MuiTab-root:not(.Mui-selected)": { color: "text.subtitle" },
  "& .MuiTab-root.Mui-selected": { color: "text.primary" },
  "& .MuiTabs-indicator": { height: 2, bgcolor: "text.primary" },
};

export const CONTROL_HEIGHT = 32;
// Neutral contained actions follow annotations/queues/annotate/review-panel.jsx.
export const PRIMARY_BUTTON_PROPS = {
  variant: "contained",
  color: "inherit",
  size: "small",
};
export const PRIMARY_BUTTON_SX = {
  height: CONTROL_HEIGHT,
  typography: "s2",
  fontWeight: "fontWeightBold",
  whiteSpace: "nowrap",
};
// Secondary actions: the theme's neutral outlined button.
export const ACTION_BUTTON_PROPS = { variant: "outlined", size: "small" };
export const ACTION_BUTTON_SX = {
  height: CONTROL_HEIGHT,
  typography: "s2",
  fontWeight: "fontWeightSemiBold",
  whiteSpace: "nowrap",
};

// warning.dark is too light for small text on white; read warnings from the
// darker step in light mode and the light step in dark mode.
export const warningText = (t) =>
  t.palette.mode === "dark"
    ? t.palette.warning.light
    : t.palette.warning.darker;

export const META_TEXT_SX = {
  typography: "s3",
  color: "text.subtitle",
  fontVariantNumeric: "tabular-nums",
};

export const visibilityLabel = (visibility) =>
  visibility === "project" ? "Shared with project" : "Private";
export const visibilityIcon = (visibility) =>
  visibility === "project"
    ? "solar:users-group-rounded-linear"
    : "solar:lock-keyhole-minimalistic-linear";
