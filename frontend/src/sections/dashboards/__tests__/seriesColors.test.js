import {
  buildBreakdownColorMap,
  buildSeriesColorMap,
} from "../seriesColors";

const hexToHsl = (hex) => {
  const clean = hex.replace("#", "");
  const n = parseInt(clean, 16);
  const r = ((n >> 16) & 255) / 255;
  const g = ((n >> 8) & 255) / 255;
  const b = (n & 255) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  switch (max) {
    case r:
      h = (g - b) / d + (g < b ? 6 : 0);
      break;
    case g:
      h = (b - r) / d + 2;
      break;
    default:
      h = (r - g) / d + 4;
      break;
  }
  return { h: h * 60, s, l };
};

const series = (name, breakdownName, metricIndex) => ({
  name,
  breakdownName,
  metricIndex,
});

describe("buildBreakdownColorMap", () => {
  it("falls back to legacy composite-label colors when there is no breakdown", () => {
    const rows = [
      series("requests (avg)", "total", 0),
      series("errors (avg)", "total", 1),
    ];
    const map = buildBreakdownColorMap(rows, { isDark: false });
    expect(map).toEqual(buildSeriesColorMap(rows.map((s) => s.name)));
  });

  it("gives one breakdown value the same base hue across metrics", () => {
    const rows = [
      series("requests / api-gateway (avg)", "api-gateway", 0),
      series("errors / api-gateway (avg)", "api-gateway", 1),
      series("requests / web-frontend (avg)", "web-frontend", 0),
      series("errors / web-frontend (avg)", "web-frontend", 1),
    ];
    const map = buildBreakdownColorMap(rows, { isDark: false });
    const hueA0 = hexToHsl(map["requests / api-gateway (avg)"]).h;
    const hueA1 = hexToHsl(map["errors / api-gateway (avg)"]).h;
    const hueB0 = hexToHsl(map["requests / web-frontend (avg)"]).h;
    const hueB1 = hexToHsl(map["errors / web-frontend (avg)"]).h;
    // Same breakdown -> same hue (the metric only shifts lightness).
    expect(Math.abs(hueA0 - hueA1)).toBeLessThan(4);
    expect(Math.abs(hueB0 - hueB1)).toBeLessThan(4);
    // Different breakdowns -> different base hues (321 vs 205).
    expect(Math.abs(hueA0 - hueB0)).toBeGreaterThan(2);
  });

  it("maps each metric to a distinct shade within one breakdown value", () => {
    const rows = [
      series("requests / project-a (avg)", "project-a", 0),
      series("errors / project-a (avg)", "project-a", 1),
      series("latency / project-a (avg)", "project-a", 2),
    ];
    const map = buildBreakdownColorMap(rows, { isDark: false });
    const colors = rows.map((s) => map[s.name]);
    expect(new Set(colors).size).toBe(rows.length);
  });

  it("applies the same shade step per metric across every breakdown value", () => {
    const rows = [
      series("requests / project-a (avg)", "project-a", 0),
      series("errors / project-a (avg)", "project-a", 1),
      series("requests / project-b (avg)", "project-b", 0),
      series("errors / project-b (avg)", "project-b", 1),
    ];
    const map = buildBreakdownColorMap(rows, { isDark: false });
    const deltaA =
      hexToHsl(map["requests / project-a (avg)"]).l -
      hexToHsl(map["errors / project-a (avg)"]).l;
    const deltaB =
      hexToHsl(map["requests / project-b (avg)"]).l -
      hexToHsl(map["errors / project-b (avg)"]).l;
    // metricIndex 1 is the same shade level regardless of the breakdown. Hex
    // output quantizes to 8-bit channels, so allow ~0.05 lightness tolerance.
    expect(deltaA).toBeCloseTo(deltaB, 1);
  });

  it("darkens shades in the light theme and lightens them in the dark theme", () => {
    const rows = [
      series("requests / project-a (avg)", "project-a", 0),
      series("errors / project-a (avg)", "project-a", 1),
    ];
    const light = buildBreakdownColorMap(rows, { isDark: false });
    const dark = buildBreakdownColorMap(rows, { isDark: true });
    const base = hexToHsl(light["requests / project-a (avg)"]).l;
    const lightShade = hexToHsl(light["errors / project-a (avg)"]).l;
    const darkShade = hexToHsl(dark["errors / project-a (avg)"]).l;
    expect(lightShade).toBeLessThan(base);
    expect(darkShade).toBeGreaterThan(base);
  });

  it("is stable across repeated calls (reload / re-query)", () => {
    const rows = [
      series("requests / project-a (avg)", "project-a", 0),
      series("errors / project-b (avg)", "project-b", 1),
    ];
    const first = buildBreakdownColorMap(rows, { isDark: false });
    const second = buildBreakdownColorMap([...rows], { isDark: false });
    expect(second).toEqual(first);
  });

  it("keeps a breakdown value's hue when other values appear", () => {
    const a = series("requests / project-a (avg)", "project-a", 0);
    const b = series("errors / project-b (avg)", "project-b", 0);
    const c = series("latency / project-c (avg)", "project-c", 0);
    const alone = buildBreakdownColorMap([a], { isDark: false });
    const withOthers = buildBreakdownColorMap([a, b, c], { isDark: false });
    expect(withOthers[a.name]).toBe(alone[a.name]);
  });

  it("uses metricIndex directly for the shade level", () => {
    const m2 = series("requests / project-a (avg)", "project-a", 2);
    const full = buildBreakdownColorMap(
      [
        series("requests / project-a (avg)", "project-a", 0),
        series("errors / project-a (avg)", "project-a", 1),
        m2,
      ],
      { isDark: false },
    );
    const sparse = buildBreakdownColorMap([m2], { isDark: false });
    expect(sparse[m2.name]).toBe(full[m2.name]);
  });
});
