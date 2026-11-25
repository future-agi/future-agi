import {
  decomposeColor,
  getContrastRatio,
  hslToRgb,
  recomposeColor,
} from "@mui/material/styles";

// The ink for text drawn on a chart fill. Plain functions, kept out of the
// component modules so Fast Refresh can reload those.

// `fill` as it shows on `surface`: a translucent fill is laid over it.
export const shownOn = (fill, surface) => {
  const color = decomposeColor(fill.startsWith("hsl") ? hslToRgb(fill) : fill);
  const opacity = color.values[3] ?? 1;
  if (opacity >= 1) return fill;
  const base = decomposeColor(surface).values;
  return recomposeColor({
    type: "rgb",
    values: [0, 1, 2].map((i) =>
      Math.round(color.values[i] * opacity + base[i] * (1 - opacity)),
    ),
  });
};

/**
 * The more readable ink for text on `fill`: the theme's primary text or the
 * page colour, one light and one dark in either mode, judged against the fill
 * as it shows on the page.
 */
export function inkOn(fill, theme) {
  const surface = theme.palette.background.paper;
  const shown = shownOn(fill, surface);
  const text = theme.palette.text.primary;
  return getContrastRatio(shown, text) >= getContrastRatio(shown, surface)
    ? text
    : surface;
}

/**
 * `inkOn` for one theme, worked out once per fill: a chart has a handful of
 * fills (a ramp's five steps) across hundreds of labelled cells.
 */
export function inkCache(theme) {
  const inks = new Map();
  return (fill) => {
    if (!inks.has(fill)) inks.set(fill, inkOn(fill, theme));
    return inks.get(fill);
  };
}
