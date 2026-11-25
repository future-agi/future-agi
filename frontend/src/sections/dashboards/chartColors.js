import { darken, lighten, rgbToHex } from "@mui/material/styles";

const hashSeriesName = (name) => {
  const s = String(name || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
};
// Name-hash gives cross-reload stability, but a bare hash % palette collides ~50%
// at 4 series. Walk each name once and, on a taken slot, advance to the next
// free one — distinct up to palette size, stable for the common non-colliding case.
// `palette` is the theme's data palette (seriesColors(theme)).
export const buildSeriesColorMap = (names, palette) => {
  const map = Object.create(null);
  const used = new Set();
  (names || []).forEach((name) => {
    const start = hashSeriesName(name) % palette.length;
    let picked = start;
    for (let i = 0; i < palette.length; i += 1) {
      const candidate = (start + i) % palette.length;
      if (!used.has(candidate)) {
        picked = candidate;
        break;
      }
    }
    used.add(picked);
    map[name] = palette[picked];
  });
  return map;
};
export const getSeriesColor = (name, map, palette) =>
  (map && map[name]) || palette[hashSeriesName(name) % palette.length];

// Start with the unmodified theme color for metric zero. Other metrics use
// different amounts of the same hue rather than consuming another palette slot.
const shadeLevels = {
  light: [0, -0.32, 0.28, -0.53, 0.48],
  dark: [0, 0.35, -0.24, 0.58, -0.43],
};

export function buildChartSeriesColorMap(series, palette, mode, chartType) {
  const colors = buildSeriesColorMap(series.map((item) => item.name), palette);
  if (!["bar", "column", "line", "stacked_bar", "stacked_column", "stacked_line"].includes(chartType)) {
    return colors;
  }
  const groups = series.filter(({ breakdownName }) => breakdownName && breakdownName !== "total");
  const hues = buildSeriesColorMap([...new Set(groups.map((item) => item.breakdownName))].sort(), palette);
  for (const { name, breakdownName, metricIndex } of groups) {
    const base = hues[breakdownName];
    const amount = (shadeLevels[mode] || shadeLevels.light)[metricIndex] || 0;
    colors[name] = amount === 0 ? base : rgbToHex(amount < 0 ? darken(base, -amount) : lighten(base, amount));
  }
  return colors;
}
