import { describe, expect, it, vi } from "vitest";
import { dsPalette } from "./ds-tokens";
import {
  getAgTheme,
  getAgThemePrompt,
  getAgThemeWithoutGrid,
} from "./ag-theme";

// Stand-in for the AG Grid theme builder: keeps the params a factory passes so
// the test can read them back.
vi.mock("ag-grid-enterprise", () => {
  const themeQuartz = {
    withPart: () => themeQuartz,
    withParams: (params) => ({ params }),
  };
  return { themeQuartz, iconSetMaterial: {} };
});

// WCAG 1.4.11: an unchecked row checkbox is drawn only by its outline, so the
// outline needs at least 3:1 against the grid and the surfaces around it.
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

const factories = [
  ["getAgTheme", getAgTheme],
  ["getAgThemeWithoutGrid", getAgThemeWithoutGrid],
  ["getAgThemePrompt", getAgThemePrompt],
];

describe.each(["light", "dark"])("AG Grid checkboxes in %s", (mode) => {
  const p = dsPalette(mode);

  describe.each(factories)("%s", (_name, factory) => {
    const { params } = factory(mode);
    const outline = params.checkboxUncheckedBorderColor;

    it("draws the unchecked outline in a solid hex colour", () => {
      expect(outline).toMatch(/^#[0-9A-Fa-f]{6}$/);
    });

    it("reaches 3:1 on the grid background", () => {
      expect(contrast(outline, params.backgroundColor)).toBeGreaterThanOrEqual(
        3,
      );
    });

    it.each(SURFACES)("reaches 3:1 on background.%s", (surface) => {
      expect(contrast(outline, p.background[surface])).toBeGreaterThanOrEqual(
        3,
      );
    });
  });
});
