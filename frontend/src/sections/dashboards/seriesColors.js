// Shared dashboard series color logic.
//
// Bar, Stacked Bar and Line color each series by its breakdown value (one
// stable base hue) plus its metric (a nearby shade). Pie and the no-breakdown
// (`total`) case keep the legacy composite-label coloring.

export const CHART_COLORS = [
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

export const hashSeriesName = (name) => {
  const s = String(name || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
};

// Name-hash gives cross-reload stability, but a bare hash % palette collides
// ~50% at 4 series. Walk each name once and, on a taken slot, advance to the
// next free one: distinct up to palette size, stable for the common case.
export const buildSeriesColorMap = (names) => {
  const map = {};
  const used = new Set();
  (names || []).forEach((name) => {
    const start = hashSeriesName(name) % CHART_COLORS.length;
    let picked = start;
    for (let i = 0; i < CHART_COLORS.length; i += 1) {
      const candidate = (start + i) % CHART_COLORS.length;
      if (!used.has(candidate)) {
        picked = candidate;
        break;
      }
    }
    used.add(picked);
    map[name] = CHART_COLORS[picked];
  });
  return map;
};

export const getSeriesColorFromMap = (map, name) =>
  (map && map[name]) || CHART_COLORS[hashSeriesName(name) % CHART_COLORS.length];

// Lightness delta (in HSL, 0..1) between adjacent metric shades.
const SHADE_STEP = 0.09;
// Keep shades off the extreme ends so they stay distinguishable from the chart
// background in both themes.
const MIN_LIGHTNESS = 0.18;
const MAX_LIGHTNESS = 0.85;

function hexToRgb(hex) {
  const clean = String(hex).replace("#", "");
  const n = parseInt(clean, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function rgbToHsl({ r, g, b }) {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case rn:
        h = (gn - bn) / d + (gn < bn ? 6 : 0);
        break;
      case gn:
        h = (bn - rn) / d + 2;
        break;
      default:
        h = (rn - gn) / d + 4;
        break;
    }
    h /= 6;
  }
  return { h: h * 360, s, l };
}

function hslToRgb(h, s, l) {
  const hn = ((((h % 360) + 360) % 360) / 360);
  const hue2rgb = (p, q, t) => {
    let tn = t;
    if (tn < 0) tn += 1;
    if (tn > 1) tn -= 1;
    if (tn < 1 / 6) return p + (q - p) * 6 * tn;
    if (tn < 1 / 2) return q;
    if (tn < 2 / 3) return p + (q - p) * (2 / 3 - tn) * 6;
    return p;
  };
  let r;
  let g;
  let b;
  if (s === 0) {
    r = g = b = l;
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, hn + 1 / 3);
    g = hue2rgb(p, q, hn);
    b = hue2rgb(p, q, hn - 1 / 3);
  }
  return { r: Math.round(r * 255), g: Math.round(g * 255), b: Math.round(b * 255) };
}

function rgbToHex({ r, g, b }) {
  const clamp = (v) => Math.max(0, Math.min(255, Math.round(v)));
  const to2 = (v) => clamp(v).toString(16).padStart(2, "0");
  return `#${to2(r)}${to2(g)}${to2(b)}`.toUpperCase();
}

// One stable base hue per breakdown value, spread across the full hue wheel.
// Derived from the name hash alone, so a value keeps its hue regardless of
// which other breakdown values are present.
const breakdownBaseHex = (breakdownName) => {
  const hue = hashSeriesName(breakdownName) % 360;
  return rgbToHex(hslToRgb(hue, 0.65, 0.52));
};

// Shift a base hue's lightness by the metric's shade position. In the light
// theme shades darken away from the light background; in the dark theme they
// lighten away from the dark background.
const shadeBaseColor = (baseHex, pos, isDark) => {
  const { r, g, b } = hexToRgb(baseHex);
  const { h, s, l } = rgbToHsl({ r, g, b });
  const delta = pos * SHADE_STEP;
  const lightness = isDark
    ? Math.min(MAX_LIGHTNESS, l + delta)
    : Math.max(MIN_LIGHTNESS, l - delta);
  return rgbToHex(hslToRgb(h, s, lightness));
};

// Build a `seriesName -> color` map (same shape as `buildSeriesColorMap`) so
// callers can drop it in without changing their lookup. Each breakdown value
// gets one stable base hue; each metric shifts that hue by a fixed lightness
// step, so a metric is always the same shade level across every breakdown.
export const buildBreakdownColorMap = (series = [], { isDark = false } = {}) => {
  const hasBreakdown = series.some((s) => s?.breakdownName !== "total");
  if (!hasBreakdown) {
    // No breakdown: keep the legacy composite-label coloring so `total` and
    // single-metric widgets render exactly as they do today.
    return buildSeriesColorMap(series.map((s) => s.name));
  }

  const map = {};
  for (const s of series) {
    const base = breakdownBaseHex(s.breakdownName);
    // metricIndex is already a stable shade key; using it directly keeps a
    // metric's shade level fixed even when another metric is hidden or absent.
    map[s.name] = shadeBaseColor(base, s.metricIndex ?? 0, isDark);
  }
  return map;
};
