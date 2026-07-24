export const DASHBOARD_SERIES_COLORS = [
  "#7B56DB",
  "#1ABCFE",
  "#FF6B6B",
  "#2ECB71",
  "#F7B731",
  "#E84393",
  "#0984E3",
  "#FD7E14",
  "#00CEC9",
  "#A29BFE",
];

const hashSeriesName = (name) => {
  const value = String(name ?? "");
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
};

export const buildSeriesColorMap = (
  names,
  palette = DASHBOARD_SERIES_COLORS,
) => {
  const colorMap = new Map();
  if (!Array.isArray(palette) || palette.length === 0) return colorMap;

  const usedSlots = new Set();
  for (const name of names || []) {
    const key = String(name ?? "");
    if (colorMap.has(key)) continue;

    const start = hashSeriesName(key) % palette.length;
    let picked = start;
    for (let offset = 0; offset < palette.length; offset += 1) {
      const candidate = (start + offset) % palette.length;
      if (!usedSlots.has(candidate)) {
        picked = candidate;
        break;
      }
    }

    usedSlots.add(picked);
    colorMap.set(key, palette[picked]);
  }
  return colorMap;
};

export const getSeriesColor = (
  colorMap,
  name,
  palette = DASHBOARD_SERIES_COLORS,
) => {
  if (!Array.isArray(palette) || palette.length === 0) return undefined;
  const key = String(name ?? "");
  return colorMap?.get(key) || palette[hashSeriesName(key) % palette.length];
};
