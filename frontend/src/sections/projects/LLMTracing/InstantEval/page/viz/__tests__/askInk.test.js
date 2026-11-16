import { describe, it, expect } from "vitest";
import {
  alpha,
  createTheme,
  decomposeColor,
  getContrastRatio,
  recomposeColor,
} from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { answerRamp, deepColor, inkOn } from "../vizParts";

// A translucent fill as the eye sees it on the page.
const onPage = (fill, page) => {
  const color = decomposeColor(fill);
  const opacity = color.values[3] ?? 1;
  const base = decomposeColor(page).values;
  return recomposeColor({
    type: "rgb",
    values: [0, 1, 2].map((i) =>
      Math.round(color.values[i] * opacity + base[i] * (1 - opacity)),
    ),
  });
};

describe.each(["light", "dark"])("text on answer fills in %s mode", (mode) => {
  const theme = createTheme({ palette: palette(mode) });
  const page = theme.palette.background.paper;
  const fills = [
    ...new Set(
      theme.palette.dataBrand.flatMap((hue) => [
        ...answerRamp(hue, mode),
        deepColor(hue),
      ]),
    ),
  ];

  it.each(fills)("stays readable on %s", (fill) => {
    const ink = inkOn(fill, theme);
    const shown = onPage(fill, page);
    const other = ink === page ? theme.palette.text.primary : page;
    expect(getContrastRatio(shown, ink)).toBeGreaterThanOrEqual(
      getContrastRatio(shown, other),
    );
    // A mid-tone fill tops out near 4.2:1 with any light or dark ink.
    expect(getContrastRatio(shown, ink)).toBeGreaterThanOrEqual(4);
  });

  it("reads a translucent fill against the page, not as solid", () => {
    const faint = alpha(theme.palette.dataBrand[0], 0.12);
    expect(inkOn(faint, theme)).toBe(theme.palette.text.primary);
  });
});

it("uses dark ink on the bright dark-mode hues", () => {
  const theme = createTheme({ palette: palette("dark") });
  const bright = theme.palette.dataBrand.filter(
    (hue) => getContrastRatio(hue, theme.palette.text.primary) < 3,
  );
  expect(bright.length).toBeGreaterThan(0);
  bright.forEach((hue) =>
    expect(inkOn(hue, theme)).toBe(theme.palette.background.paper),
  );
});
