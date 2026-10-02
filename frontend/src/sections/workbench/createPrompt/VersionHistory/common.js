const SYSTEM_PROMPTS = ["Production", "Staging", "Development"];

export const getColorMap = (name, theme) => {
  const isDark = theme.palette.mode === "dark";
  if (SYSTEM_PROMPTS.includes(name)) {
    const systemColors = {
      Production: {
        backgroundColor: isDark
          ? theme.palette.green.o10
          : theme.palette.green[50],
        hoverBackgroundColor: isDark
          ? theme.palette.green.o20
          : theme.palette.green[100],
        color: isDark ? theme.palette.green[400] : theme.palette.green[700],
      },
      Staging: {
        backgroundColor: isDark
          ? theme.palette.orange.o10
          : theme.palette.orange[50],
        hoverBackgroundColor: isDark
          ? theme.palette.orange.o20
          : theme.palette.orange[100],
        color: isDark ? theme.palette.orange[400] : theme.palette.orange[700],
      },
      Development: {
        backgroundColor: isDark
          ? theme.palette.blue.o10
          : theme.palette.blue[50],
        hoverBackgroundColor: isDark
          ? theme.palette.blue.o20
          : theme.palette.blue[100],
        color: isDark ? theme.palette.blue[400] : theme.palette.blue[700],
      },
    };

    return systemColors[name];
  }
  return {
    backgroundColor: theme.palette.action.hover,
    hoverBackgroundColor: theme.palette.action.selected,
    color: theme.palette.text.primary,
  };
};
