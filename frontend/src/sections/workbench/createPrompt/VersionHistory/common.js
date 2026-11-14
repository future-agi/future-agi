import { statusColors } from "src/utils/runStatus";

const SYSTEM_PROMPTS = ["Production", "Staging", "Development"];

const SYSTEM_PROMPT_ROLES = {
  Production: "success",
  Staging: "warning",
  Development: "info",
};

export const getColorMap = (name, theme) => {
  if (SYSTEM_PROMPTS.includes(name)) {
    const c = statusColors(theme, SYSTEM_PROMPT_ROLES[name]);
    return {
      backgroundColor: c.bg,
      hoverBackgroundColor: c.border,
      color: c.fg,
    };
  }
  return {
    backgroundColor: theme.palette.action.hover,
    hoverBackgroundColor: theme.palette.action.selected,
    color: theme.palette.text.primary,
  };
};
