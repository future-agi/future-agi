// Split colours (area-datasets §2, C13): 8 palette keys stored on the split
// (`color`), rendered as a small chip with an alpha 0.12 background and the
// palette's `.dark` text (`.light` in dark mode).
import { alpha, darken, getContrastRatio, lighten } from "@mui/material/styles";

/** DL#47: chip text keeps at least this contrast on its own tint. */
export const MIN_CHIP_CONTRAST = 4.5;

const hexRgb = (hex) => {
  const h = String(hex).replace("#", "");
  const f = h.length === 3 ? h.replace(/./g, "$&$&") : h.slice(0, 6);
  const n = parseInt(f, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

/** `color` at `a` opacity over the solid `under` (both hex), as hex. */
export function blendOver(color, a, under) {
  const c = hexRgb(color);
  const u = hexRgb(under);
  const mix = c.map((v, i) => Math.round(v * a + u[i] * (1 - a)));
  return `#${mix.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/**
 * DL#47: `fg` darkened (light mode) or lightened (dark mode) in small steps
 * until it reads at MIN_CHIP_CONTRAST on `bg` (a solid hex).
 */
export function readableOn(fg, bg, dark) {
  let c = fg;
  for (let i = 0; i < 10; i += 1) {
    if (getContrastRatio(c, bg) >= MIN_CHIP_CONTRAST) return c;
    c = dark ? lighten(c, 0.15) : darken(c, 0.15);
  }
  return dark ? "#FFFFFF" : "#000000";
}

/** Keys a split's `color` may take, in the order new splits get them. */
export const SPLIT_COLOR_KEYS = [
  "info",
  "secondary",
  "error",
  "warning",
  "success",
  "primary",
  "grey",
  "dark",
];

/** Human names for the colour picker. */
export const SPLIT_COLOR_LABELS = {
  info: "Blue",
  secondary: "Purple",
  error: "Red",
  warning: "Amber",
  success: "Green",
  primary: "Violet",
  grey: "Grey",
  dark: "Graphite",
};

/** A valid key for any input (unknown -> "grey"). */
export const splitColorKey = (key) =>
  SPLIT_COLOR_KEYS.includes(key) ? key : "grey";

/** First colour not used yet by `existing` splits, cycling when all are. */
export function nextSplitColor(existing = []) {
  const used = new Set(existing.map((s) => s?.color).filter(Boolean));
  return (
    SPLIT_COLOR_KEYS.find((k) => !used.has(k)) ??
    SPLIT_COLOR_KEYS[existing.length % SPLIT_COLOR_KEYS.length]
  );
}

/**
 * Chip colours for a split colour key.
 * @returns {{bg: string, fg: string, border: string, dot: string}}
 */
export function splitChipColors(key, theme) {
  const k = splitColorKey(key);
  const dark = theme?.palette?.mode === "dark";
  if (k === "grey" || k === "dark") {
    const base =
      k === "grey"
        ? theme?.palette?.grey?.[500] ?? "#919EAB"
        : theme?.palette?.text?.primary ?? "#1A1A1A";
    return {
      bg: alpha(base, k === "grey" ? 0.16 : 0.1),
      fg: k === "grey" ? theme?.palette?.text?.secondary ?? "#262626" : base,
      border: alpha(base, 0.24),
      dot: base,
    };
  }
  const p = theme?.palette?.[k] ?? {};
  const main = p.main ?? "#919EAB";
  const paper =
    theme?.palette?.background?.paper ?? (dark ? "#111111" : "#FFFFFF");
  const fg = (dark ? p.light : p.dark) ?? main;
  const solidBg =
    /^#[0-9a-f]{3,8}$/i.test(main) && /^#[0-9a-f]{3,8}$/i.test(paper)
      ? blendOver(main, 0.12, paper)
      : null;
  return {
    bg: alpha(main, 0.12),
    fg: solidBg && /^#/.test(fg) ? readableOn(fg, solidBg, dark) : fg,
    border: alpha(main, dark ? 0.4 : 0.32),
    dot: main,
  };
}
