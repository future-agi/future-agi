import { alpha } from "@mui/material/styles";

// Text sizes on the design system scale: headings, primary text, secondary text.
export const T14 = { typography: "m1" };
export const T13 = { typography: "s1" };
export const T12 = { typography: "s3" };

export const PANEL_SX = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  bgcolor: "background.paper",
};

export const ROW_BOX_SX = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "6px",
};

// Buttons take their size from the theme (size="small" is 28px).
export const BTN_SX = { minWidth: 0 };

export const OUTLINED_BTN_SX = BTN_SX;

// A span inside a clickable row that reads as a small outlined button.
export const BUTTON_LOOK_SX = {
  height: 28,
  px: 1.25,
  borderRadius: "6px",
  border: "1px solid",
  borderColor: "divider",
  bgcolor: "background.paper",
  color: "text.primary",
  typography: "s1",
  fontWeight: 500,
  whiteSpace: "nowrap",
};

// The catalog's per-row Connect.
export const CONNECT_BTN_SX = { ...BUTTON_LOOK_SX, minWidth: 70 };

// A selectable chip: tabs, Voice/Chat, Public/Private.
export const chipSx = (on) => ({
  display: "inline-flex",
  alignItems: "center",
  gap: 0.75,
  height: 30,
  px: 1.25,
  borderRadius: "6px",
  border: "1px solid",
  borderColor: on ? "text.primary" : "divider",
  bgcolor: (t) =>
    on ? alpha(t.palette.text.primary, 0.08) : t.palette.background.paper,
  color: "text.primary",
  ...T12,
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
  "&:hover": {
    borderColor: on
      ? "text.primary"
      : (t) => alpha(t.palette.text.primary, 0.3),
  },
  "&.Mui-disabled": { cursor: "default", color: "text.disabled" },
});

// Inputs keep the theme's small size; values read in monospace.
export const INPUT_SX = {
  "& .MuiInputBase-input": {
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  },
  "& .MuiInputBase-inputMultiline": { fontFamily: "inherit" },
};

export const focusRing = (t) => ({
  outline: `2px solid ${alpha(t.palette.text.primary, 0.5)}`,
  outlineOffset: 2,
});

// An inline text link in a muted line ("Tools on an MCP server? Use them…").
export const LINK_SX = {
  ...T12,
  verticalAlign: "baseline",
  color: "text.primary",
  fontWeight: 600,
  "&:hover": { textDecoration: "underline" },
  "&.Mui-focusVisible": focusRing,
};
