import { createTheme, getContrastRatio } from "@mui/material/styles";
import { palette } from "src/theme/palette";

// The two app themes, built from the palette the app itself uses.
export const PROMPT_THEMES = ["light", "dark"].map((mode) => [
  mode,
  createTheme({ palette: palette(mode) }),
]);

// WCAG 2.2 AA for normal-size text.
export const AA_TEXT = 4.5;

// Contrast of the colour an element is painted with against the page paper.
export function contrastOnPaper(element, theme) {
  return getContrastRatio(
    getComputedStyle(element).color,
    theme.palette.background.paper,
  );
}

// The nearest element that holds both nodes (a row and its children).
export function rowOf(first, second) {
  let row = first.parentElement;
  while (row && !row.contains(second)) row = row.parentElement;
  return row;
}

// jsdom reports `min-width: 0` as "0"; a browser reports "0px".
export const isZero = (value) => value === "0" || value === "0px";
