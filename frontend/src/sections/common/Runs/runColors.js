// Run colours (PRODUCT-SPEC §4; R3 G0 §B11, R2D-41). The baseline run takes
// index 0, then the remaining runs in view order. A run keeps its colour on
// every surface of one page (chips, headers, charts, side by side lanes).
//
// R3: colour-blind safe. The R2 palette (getUniqueColorPalette) put pink
// next to blue (CIEDE2000 ΔE 2.0 under deuteranopia) and repeated pink at
// index 5. RUN_PALETTE below is checked in __tests__/runColors.cvd.test.js:
// every pair of the first 8 colours has ΔE2000 ≥ 10 under normal vision,
// deuteranopia and protanopia (Machado 2009, severity 1.0; min 10.6). The
// order of hues is kept (pink, blue, orange, green, purple, …); colours are
// mid-tones so they read on white and on the dark background; dark mode
// swaps only index 7 (olive → yellow) for contrast. Beyond 8 runs the
// colours repeat.
import { useMemo } from "react";
import { alpha, useTheme } from "@mui/material/styles";

/** The 8 run colours, light and dark mode (same order). */
export const RUN_PALETTE = Object.freeze({
  light: Object.freeze([
    "#9F46B5", // pink.700 (baseline)
    "#78B4F9", // blue.300
    "#F49A54", // orange.300
    "#008F47", // green.600
    "#856EFF", // purple.400
    "#0E9AA7", // teal
    "#14B8A6", // teal.400
    "#625C26", // olive (warning.darker)
  ]),
  dark: Object.freeze([
    "#9F46B5",
    "#78B4F9",
    "#F49A54",
    "#008F47",
    "#856EFF",
    "#0E9AA7",
    "#14B8A6",
    "#FFCC1A", // yellow.400
  ]),
});

const relLum = (hex) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = c.map((v) =>
    v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
/** Text colour on a solid: dark ink on light colours, white otherwise. */
const fgOn = (hex) => (relLum(hex) > 0.36 ? "#1A1A1A" : "#FFFFFF");

/**
 * @typedef {object} RunColor
 * @property {number} index  position in the palette (baseline = 0)
 * @property {string} solid  strong colour: 3 px bar, letter square, series
 * @property {string} tint   the solid colour at alpha 0.12 (backgrounds)
 * @property {string} fg     text colour on `solid`
 * @property {string} light  pale variant (chart fills)
 * @property {{solid, tint, fg, light}} dark  the dark-mode variant
 */

const variant = (solid) => ({
  solid,
  tint: alpha(solid, 0.12),
  fg: fgOn(solid),
  light: alpha(solid, 0.32),
});

/**
 * Colour for a palette index. `mode: "dark"` returns the dark variant as
 * the top-level fields (every RunColor also carries `.dark`).
 * @param {number} index
 * @param {"light"|"dark"} [mode="light"]
 * @returns {RunColor}
 */
export function runColorAt(index, mode = "light") {
  const i = Math.max(0, index | 0) % RUN_PALETTE.light.length;
  const light = variant(RUN_PALETTE.light[i]);
  const dark = variant(RUN_PALETTE.dark[i]);
  return { index, ...(mode === "dark" ? dark : light), dark };
}

/**
 * @param {string[]} runIds  runs in view order (duplicates and empties ignored)
 * @param {string|null} [baselineId]  takes index 0 when it is in `runIds`
 * @param {{mode?: "light"|"dark"}} [opts]
 * @returns {Object<string, RunColor>}
 */
export function runColorMap(runIds, baselineId, { mode = "light" } = {}) {
  const ids = [...new Set((runIds ?? []).filter(Boolean))];
  const ordered =
    baselineId && ids.includes(baselineId)
      ? [baselineId, ...ids.filter((id) => id !== baselineId)]
      : ids;
  return Object.fromEntries(ordered.map((id, i) => [id, runColorAt(i, mode)]));
}

/** Memoised runColorMap for a component (follows the theme mode). */
export function useRunColors(runIds, baselineId) {
  const theme = useTheme();
  const mode = theme?.palette?.mode === "dark" ? "dark" : "light";
  const key = (runIds ?? []).join(",");
  return useMemo(
    () => runColorMap(runIds, baselineId, { mode }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, baselineId, mode],
  );
}
