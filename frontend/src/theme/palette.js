import * as legacy from "./legacy-palette";
import { dsPalette } from "./ds-tokens";

// Compatibility boundary: unmigrated ramps and syntax remain available until
// rollout step 2. All semantic roles come from the unmodified product-design pin.
export * from "./legacy-palette";
const light = dsPalette("light");
export const grey = light.grey;
export const primary = { ...legacy.primary, ...light.primary };
export const secondary = { ...legacy.secondary, ...light.secondary };
export const info = { ...legacy.info, ...light.info };
export const success = { ...legacy.success, ...light.success };
export const warning = { ...legacy.warning, ...light.warning };
export const error = { ...legacy.error, ...light.error };
export const action = light.action;

// Text-on-tint label tokens (a11y #14). Light: warning.dark and error.main
// are used as text on their soft chips (#FEFCEC, #FAEEEE); the shipped
// #B8AC47 / #DB2F2D read at 2.3:1 / 4.2:1 there. Dark: the same tokens sit on
// #111 / #18181b and dark tinted chips, so they are lifted instead.
export const LIGHT_TEXT_DISABLED = "#6B6B6B";
export const LABEL_TOKENS = Object.freeze({
  light: { warningDark: "#7A6F14", errorMain: "#C62828" },
  dark: { warningDark: "#E5D97A", errorMain: "#E5484D" },
});

export function palette(mode) {
  const p = dsPalette(mode);
  const old = legacy.palette(p.mode);
  return {
    ...old,
    ...p,
    mode: /** @type {"light" | "dark"} */ (p.mode),
    primary: { ...old.primary, ...p.primary },
    info: {
      ...old.info,
      ...p.info,
      alert: p.status.info.bg,
      icon: p.status.info.fg,
    },
    success: {
      ...old.success,
      ...p.success,
      alert: p.status.success.bg,
      icon: p.status.success.fg,
    },
    warning: {
      ...old.warning,
      ...p.warning,
      alert: p.status.warning.bg,
      icon: p.status.warning.fg,
    },
    error: {
      ...old.error,
      ...p.error,
      alert: p.status.danger.bg,
      icon: p.status.danger.fg,
    },
    background: {
      ...p.background,
      aiSurface: p.status.discovery.bg,
      aiHover: p.action.hover,
    },
    border: {
      ...p.border,
      active: p.border.strong,
      bright: p.text.tertiary,
      light: p.border.default,
    },
    whiteScale: {
      50: p.background.paper,
      100: p.background.default,
      200: p.background.subtle,
      300: p.background.muted,
      400: p.border.default,
      500: p.border.strong,
    },
    accent: {
      ...old.accent,
      brand: p.status.discovery.fg,
      pass: p.status.success.fg,
      fail: p.status.danger.fg,
      agent: p.status.discovery.fg,
      info: p.status.info.fg,
      neutral: p.status.neutral.fg,
    },
  };
}
