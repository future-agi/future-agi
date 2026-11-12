import { createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";
import { palette } from "src/theme/palette";
import { checkbox } from "../checkbox";
import { radio } from "../radio";

// WCAG 1.4.11: the outline of an unchecked checkbox or radio is the only thing
// that shows the control is there, so it needs at least 3:1 against every
// surface the control sits on.
const channel = (value) => {
  const c = value / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};

const luminance = (hex) => {
  const [r, g, b] = hex
    .slice(1)
    .match(/../g)
    .map((v) => parseInt(v, 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
};

const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

const SURFACES = ["paper", "default", "neutral", "muted", "overlay"];

describe.each(["light", "dark"])("unchecked controls in %s", (mode) => {
  const theme = createTheme({ palette: palette(mode) });
  const controls = [
    ["checkbox", checkbox(theme).MuiCheckbox.styleOverrides.root.color],
    ["radio", radio(theme).MuiRadio.styleOverrides.root.color],
  ];

  describe.each(controls)("%s outline", (_name, color) => {
    it("is a solid hex colour", () => {
      expect(color).toMatch(/^#[0-9A-Fa-f]{6}$/);
    });

    it.each(SURFACES)("reaches 3:1 on background.%s", (surface) => {
      const background = theme.palette.background[surface];
      expect(contrast(color, background)).toBeGreaterThanOrEqual(3);
    });
  });
});
