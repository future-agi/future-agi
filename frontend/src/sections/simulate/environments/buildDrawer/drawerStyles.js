import { alpha } from "@mui/material/styles";

/*
  Sizes from Vel's Build environment drawer, measured in the prototype. The
  app theme's own small buttons and inputs are bigger, so the drawer sets
  them here.
*/

export const T14 = { fontSize: 14, lineHeight: "22px" };
export const T13 = { fontSize: 13, lineHeight: "20px" };
export const T12 = { fontSize: 12, lineHeight: "16px" };

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

// 28px buttons, 12px medium text.
export const BTN_SX = {
  height: 28,
  minWidth: 0,
  px: 1.25,
  borderRadius: "6px",
  ...T12,
  fontWeight: 500,
  whiteSpace: "nowrap",
  boxShadow: "none",
};

export const OUTLINED_BTN_SX = {
  ...BTN_SX,
  color: "text.primary",
  borderColor: "divider",
  bgcolor: "background.paper",
};

// The catalog's per-row Connect.
export const CONNECT_BTN_SX = {
  ...BTN_SX,
  minWidth: 70,
  borderRadius: "4px",
  fontWeight: 600,
  color: "text.primary",
  border: "1px solid",
  borderColor: "divider",
};

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

// The 28px inputs, 12px monospace.
export const INPUT_SX = {
  "& .MuiInputBase-root": {
    minHeight: 28,
    borderRadius: "4px",
    ...T12,
  },
  "& .MuiInputBase-input": {
    py: 0.75,
    px: 1,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    ...T12,
  },
  "& .MuiInputBase-inputMultiline": { fontFamily: "inherit" },
  "& .MuiOutlinedInput-notchedOutline": { borderColor: "divider" },
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
