// Entry cover tiles (manifest `cover: {emoji, from, to}`) and publisher
// monograms. Self-drawn, so a Hub page loads no third-party image.

const COLORS = {
  slate: "#64748B",
  gray: "#6B7280",
  zinc: "#71717A",
  stone: "#78716C",
  red: "#EF4444",
  orange: "#F97316",
  amber: "#F59E0B",
  yellow: "#EAB308",
  lime: "#84CC16",
  green: "#22C55E",
  emerald: "#10B981",
  teal: "#14B8A6",
  cyan: "#06B6D4",
  sky: "#0EA5E9",
  blue: "#3B82F6",
  indigo: "#6366F1",
  violet: "#8B5CF6",
  purple: "#A855F7",
  fuchsia: "#D946EF",
  pink: "#EC4899",
  rose: "#F43F5E",
};

const FALLBACK = ["#7857FC", "#6366F1"];

export function coverColors(cover) {
  const from = COLORS[cover?.from] || FALLBACK[0];
  const to = COLORS[cover?.to] || FALLBACK[1];
  return [from, to];
}

export function coverEmoji(cover) {
  const emoji = typeof cover?.emoji === "string" ? cover.emoji : "";
  return emoji.slice(0, 4) || "◆";
}

const MONO = ["#7857FC", "#0EA5E9", "#10B981", "#F59E0B", "#EC4899", "#6366F1", "#14B8A6"];

export function monogram(name, handle) {
  const source = String(name || handle || "?").trim();
  const letter = source.charAt(0).toUpperCase() || "?";
  let hash = 0;
  for (const ch of String(handle || source)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return { letter, color: MONO[hash % MONO.length] };
}
