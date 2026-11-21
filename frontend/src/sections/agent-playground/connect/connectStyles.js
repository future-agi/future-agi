import { alpha } from "@mui/material/styles";

export const PANEL_SX = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  bgcolor: "background.paper",
  overflow: "hidden",
};

export const MONO = "ui-monospace, Menlo, monospace";

export const focusRing = (theme) => ({
  outline: `2px solid ${alpha(theme.palette.text.primary, 0.5)}`,
  outlineOffset: 2,
});
