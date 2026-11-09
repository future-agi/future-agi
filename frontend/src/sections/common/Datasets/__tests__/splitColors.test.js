import { describe, expect, it } from "vitest";
import { createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { getContrastRatio } from "@mui/material/styles";
import {
  MIN_CHIP_CONTRAST,
  blendOver,
  SPLIT_COLOR_KEYS,
  SPLIT_COLOR_LABELS,
  nextSplitColor,
  splitChipColors,
  splitColorKey,
} from "../splitColors";

const theme = createTheme({ palette: palette("light") });

describe("splitColors", () => {
  it("has 8 keys, each with a label, covering the world's split colours", () => {
    expect(SPLIT_COLOR_KEYS).toHaveLength(8);
    SPLIT_COLOR_KEYS.forEach((k) => expect(SPLIT_COLOR_LABELS[k]).toBeTruthy());
    ["info", "secondary", "error", "warning"].forEach((k) =>
      expect(SPLIT_COLOR_KEYS).toContain(k),
    );
  });

  it("falls back to grey for unknown keys", () => {
    expect(splitColorKey("magenta")).toBe("grey");
    expect(splitColorKey("error")).toBe("error");
  });

  it("picks the next unused colour", () => {
    expect(nextSplitColor([])).toBe("info");
    expect(nextSplitColor([{ color: "info" }, { color: "secondary" }])).toBe(
      "error",
    );
    const all = SPLIT_COLOR_KEYS.map((color) => ({ color }));
    expect(SPLIT_COLOR_KEYS).toContain(nextSplitColor(all));
  });

  it("tints at alpha 0.12 with dark text", () => {
    const c = splitChipColors("error", theme);
    expect(c.bg).toMatch(/rgba\(.*0\.12\)/);
    expect(c.fg).toBe(theme.palette.error.dark);
    const g = splitChipColors("grey", theme);
    expect(g.bg).toMatch(/rgba/);
    expect(g.fg).toBe(theme.palette.text.secondary);
  });

  it("DL#47: chip text reads at >= 4.5:1 on its tint in both themes", () => {
    const dark = createTheme({ palette: palette("dark") });
    [theme, dark].forEach((t) => {
      SPLIT_COLOR_KEYS.filter((k) => !["grey", "dark"].includes(k)).forEach(
        (k) => {
          const c = splitChipColors(k, t);
          const bg = blendOver(
            t.palette[k].main,
            0.12,
            t.palette.background.paper,
          );
          expect(getContrastRatio(c.fg, bg)).toBeGreaterThanOrEqual(
            MIN_CHIP_CONTRAST,
          );
        },
      );
    });
  });

  it("blends a colour over a solid background", () => {
    expect(blendOver("#000000", 0.5, "#FFFFFF")).toBe("#808080");
  });
});
