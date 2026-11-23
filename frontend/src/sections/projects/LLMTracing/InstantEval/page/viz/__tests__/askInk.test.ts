import { describe, it, expect } from "vitest";
import {
  alpha,
  createTheme,
  decomposeColor,
  getContrastRatio,
  recomposeColor,
} from "@mui/material/styles";
import type { Theme, ThemeOptions } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { inkCache, inkOn } from "../chartInk";
import { answerRamp, deepColor } from "../vizParts";
import {
  CATEGORY_COLORS,
  CATEGORY_COLORS_DARK,
} from "../../../results/answerColors";

// A translucent fill as the eye sees it on the page.
const onPage = (fill: string, page: string) => {
  const color = decomposeColor(fill);
  const opacity = color.values[3] ?? 1;
  const base = decomposeColor(page).values;
  return recomposeColor({
    type: "rgb",
    values: [0, 1, 2].map((i) =>
      Math.round(color.values[i] * opacity + base[i] * (1 - opacity)),
    ) as [number, number, number],
  });
};

// The theme with its brand chart set, which MUI's own types don't carry.
type BrandTheme = Theme & {
  palette: Theme["palette"] & { dataBrand: string[] };
};
const themeFor = (mode: "light" | "dark") =>
  createTheme({ palette: palette(mode) } as ThemeOptions) as BrandTheme;

describe.each(["light", "dark"] as const)(
  "text on answer fills in %s mode",
  (mode) => {
    const theme = themeFor(mode);
    const page = theme.palette.background.paper;
    // Every fill that carries text: each answer hue's five heatmap steps (Yes,
    // No, the category hues and the neutral catch-all), and the timeline's "+N"
    // badge.
    const hues = [
      ...theme.palette.dataBrand,
      ...(mode === "dark" ? CATEGORY_COLORS_DARK : CATEGORY_COLORS),
      theme.palette.text.disabled,
    ];
    const fills = [
      ...new Set([
        ...hues.flatMap((hue) => [...answerRamp(hue, mode), deepColor(hue)]),
        theme.palette.text.secondary,
      ]),
    ];

    it.each(fills)("stays readable on %s", (fill) => {
      const ink = inkOn(fill, theme);
      const shown = onPage(fill, page);
      const other = ink === page ? theme.palette.text.primary : page;
      expect(getContrastRatio(shown, ink)).toBeGreaterThanOrEqual(
        getContrastRatio(shown, other),
      );
      // Text needs 4.5:1, so no step may sit near the midpoint between the
      // two inks, where the better of them tops out near 4.2:1.
      expect(getContrastRatio(shown, ink)).toBeGreaterThanOrEqual(4.5);
    });

    it("works out the ink once per fill", () => {
      const inkFor = inkCache(theme);
      fills.forEach((fill) => expect(inkFor(fill)).toBe(inkOn(fill, theme)));
    });

    it("reads a translucent fill against the page, not as solid", () => {
      const faint = alpha(theme.palette.dataBrand[0], 0.12);
      expect(inkOn(faint, theme)).toBe(theme.palette.text.primary);
    });
  },
);

it("uses dark ink on the bright dark-mode hues", () => {
  const theme = themeFor("dark");
  const bright = theme.palette.dataBrand.filter(
    (hue) => getContrastRatio(hue, theme.palette.text.primary) < 3,
  );
  expect(bright.length).toBeGreaterThan(0);
  bright.forEach((hue) =>
    expect(inkOn(hue, theme)).toBe(theme.palette.background.paper),
  );
});
