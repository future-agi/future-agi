/**
 * Shared dark-mode elevation styles for floating surfaces (dialogs, popovers, menus, etc.).
 *
 * Returns an empty object in light mode so it can be spread unconditionally:
 *   ...darkElevatedStyles(theme)
 *
 * @param {object} theme - MUI theme
 * @param {object} [options]
 * @param {string} [options.boxShadow] - Override the default dark shadow
 * @param {boolean} [options.borderOnly] - Only apply border, skip bg/shadow
 */
export function darkElevatedStyles(theme, { boxShadow, borderOnly } = {}) {
  if (theme.palette.mode === "light") return {};

  const border = theme.palette.border?.default || "#303036";

  if (borderOnly) {
    return { border: `1px solid ${border}` };
  }

  return {
    border: `1px solid ${border}`,
    backgroundColor: theme.palette.background.overlay,
    "--input-surface": theme.palette.background.overlay,
    backgroundImage: "none",
    boxShadow: boxShadow || theme.customShadows.dropdown,
  };
}
