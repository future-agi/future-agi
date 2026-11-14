import { describe, expect, it } from "vitest";
import { createTheme, getContrastRatio } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { monoSeriesColors, seriesLabelColors } from "src/theme/data-colors";
import tokens from "../../../design-system/tokens/tokens.json";

const themeFor = (mode) => createTheme({ palette: palette(mode) });

const purpleRamp = new Set(Object.values(tokens.primitive.purple));

describe("monoSeriesColors", () => {
  it.each(["light", "dark"])(
    "gives eight distinct brand-purple shades in %s mode",
    (mode) => {
      const colors = monoSeriesColors(themeFor(mode));
      expect(colors).toHaveLength(8);
      expect(new Set(colors).size).toBe(8);
      colors.forEach((color) => expect(purpleRamp.has(color)).toBe(true));
    },
  );

  it("leads with the brand purple used by data.1", () => {
    for (const mode of ["light", "dark"]) {
      const theme = themeFor(mode);
      expect(monoSeriesColors(theme)[0]).toBe(theme.palette.data[0]);
    }
  });
});

describe("seriesLabelColors", () => {
  it("puts light text on dark shades and dark text on light shades", () => {
    const theme = themeFor("light");
    const { purple } = tokens.primitive;
    const [onDark, onLight] = seriesLabelColors(theme, [
      purple["900"],
      purple["200"],
    ]);
    expect(onDark).not.toBe(onLight);
    expect(getContrastRatio(purple["900"], onDark)).toBeGreaterThan(
      getContrastRatio(purple["900"], onLight),
    );
    expect(getContrastRatio(purple["200"], onLight)).toBeGreaterThan(
      getContrastRatio(purple["200"], onDark),
    );
  });
});
