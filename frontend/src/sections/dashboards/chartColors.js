export const SERIES_COLORS = [
  "#7B56DB", // purple (primary)
  "#1ABCFE", // cyan
  "#FF6B6B", // coral red
  "#2ECB71", // emerald green
  "#F7B731", // amber
  "#E84393", // magenta pink
  "#0984E3", // ocean blue
  "#FD7E14", // tangerine orange
  "#00CEC9", // teal
  "#A29BFE", // lavender
];

const METRIC_LIGHTNESS = {
  light: [0.42, 0.3, 0.54, 0.24, 0.64],
  dark: [0.64, 0.76, 0.54, 0.84, 0.44],
};

const hashSeriesName = (name) => {
  const value = String(name || "");
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
};

// Name hashing keeps colors stable across reloads. Walking to the next free
// palette slot avoids collisions for the common case of at most ten entries.
export const buildSeriesColorMap = (names) => {
  const map = {};
  const used = new Set();
  (names || []).forEach((name) => {
    const start = hashSeriesName(name) % SERIES_COLORS.length;
    let picked = start;
    for (let index = 0; index < SERIES_COLORS.length; index += 1) {
      const candidate = (start + index) % SERIES_COLORS.length;
      if (!used.has(candidate)) {
        picked = candidate;
        break;
      }
    }
    used.add(picked);
    map[name] = SERIES_COLORS[picked];
  });
  return map;
};

export const getSeriesColorFromMap = (map, name) =>
  (map && map[name]) ||
  SERIES_COLORS[hashSeriesName(name) % SERIES_COLORS.length];

const hexToHsl = (hex) => {
  const raw = String(hex).replace("#", "");
  const red = parseInt(raw.slice(0, 2), 16) / 255;
  const green = parseInt(raw.slice(2, 4), 16) / 255;
  const blue = parseInt(raw.slice(4, 6), 16) / 255;
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const lightness = (max + min) / 2;

  if (max === min) return { hue: 0, saturation: 0, lightness };

  const delta = max - min;
  const saturation =
    lightness > 0.5 ? delta / (2 - max - min) : delta / (max + min);
  let hue;
  if (max === red) {
    hue = (green - blue) / delta + (green < blue ? 6 : 0);
  } else if (max === green) {
    hue = (blue - red) / delta + 2;
  } else {
    hue = (red - green) / delta + 4;
  }
  return { hue: hue / 6, saturation, lightness };
};

const hueToRgb = (p, q, rawHue) => {
  let hue = rawHue;
  if (hue < 0) hue += 1;
  if (hue > 1) hue -= 1;
  if (hue < 1 / 6) return p + (q - p) * 6 * hue;
  if (hue < 1 / 2) return q;
  if (hue < 2 / 3) return p + (q - p) * (2 / 3 - hue) * 6;
  return p;
};

const hslToHex = ({ hue, saturation, lightness }) => {
  let red = lightness;
  let green = lightness;
  let blue = lightness;
  if (saturation !== 0) {
    const q =
      lightness < 0.5
        ? lightness * (1 + saturation)
        : lightness + saturation - lightness * saturation;
    const p = 2 * lightness - q;
    red = hueToRgb(p, q, hue + 1 / 3);
    green = hueToRgb(p, q, hue);
    blue = hueToRgb(p, q, hue - 1 / 3);
  }
  const channel = (value) =>
    Math.round(value * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${channel(red)}${channel(green)}${channel(blue)}`.toUpperCase();
};

// The editor permits at most five metrics. Each metric therefore owns one
// lightness level, while the breakdown's palette color supplies hue and
// saturation. Light charts use darker levels; dark charts use lighter levels.
export const shadeSeriesColor = (
  baseColor,
  metricIndex,
  colorMode = "light",
) => {
  const levels = METRIC_LIGHTNESS[colorMode] || METRIC_LIGHTNESS.light;
  const index = Math.abs(Number(metricIndex) || 0) % levels.length;
  const hsl = hexToHsl(baseColor);
  return hslToHex({ ...hsl, lightness: levels[index] });
};

// Resolve Bar, Stacked Bar and Line colors from the full response series so
// hidden entries retain their colors. Without a breakdown, preserve the legacy
// name-based mapping exactly. With a breakdown, raw breakdown values select a
// stable base hue and metricIndex selects a consistent nearby shade.
export const buildChartSeriesColorMap = (series, colorMode = "light") => {
  const entries = series || [];
  const legacyMap = buildSeriesColorMap(entries.map((item) => item.name));
  const hasBreakdown = entries.some((item) => item.breakdownName !== "total");
  if (!hasBreakdown) return legacyMap;

  const breakdownNames = [
    ...new Set(entries.map((item) => item.breakdownName)),
  ].sort((left, right) => String(left).localeCompare(String(right)));
  const breakdownColorMap = buildSeriesColorMap(breakdownNames);

  return entries.reduce((map, item) => {
    const baseColor = getSeriesColorFromMap(
      breakdownColorMap,
      item.breakdownName,
    );
    map[item.name] = shadeSeriesColor(baseColor, item.metricIndex, colorMode);
    return map;
  }, {});
};
