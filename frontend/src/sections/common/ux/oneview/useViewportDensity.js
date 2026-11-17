// Viewport-aware DEFAULTS (ONE-VIEW-SPEC §3.10). The window height picks
// defaults only (which sections start open, default row density, how many
// lanes); it never hides a feature, and a user's explicit choice wins.
//
//   const density = useViewportDensity(); // "short" | "regular" | "tall"
//   <Section defaultOpen="roomy" …/>      // open unless "short"
import useMediaQuery from "@mui/material/useMediaQuery";

/** 1280×720, 1366×768 laptops (moved here from task-compare/tcMode.js). */
export const SHORT_SCREEN = "(max-height:819px)";
/** 1920×1080 and taller. */
export const TALL_SCREEN = "(min-height:1080px)";

export const SHORT_MAX_HEIGHT = 819;
export const TALL_MIN_HEIGHT = 1080;

/** Pure: `"short"` (< 820), `"regular"` (820–1079), `"tall"` (≥ 1080). */
export function viewportDensityOf(height) {
  const h = Number(height);
  if (!Number.isFinite(h) || h <= 0) return "regular";
  if (h <= SHORT_MAX_HEIGHT) return "short";
  if (h >= TALL_MIN_HEIGHT) return "tall";
  return "regular";
}

/** @returns {"short"|"regular"|"tall"} */
export default function useViewportDensity() {
  const short = useMediaQuery(SHORT_SCREEN, { noSsr: true });
  const tall = useMediaQuery(TALL_SCREEN, { noSsr: true });
  if (short) return "short";
  if (tall) return "tall";
  return "regular";
}

export { useViewportDensity };
