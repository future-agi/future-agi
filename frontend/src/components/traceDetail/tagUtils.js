/**
 * Shared tag color utilities.
 * Tags can be plain strings (legacy) or objects { name, color }.
 */

export const TAG_COLORS = [
  "#8B5CF6", // purple
  "#3B82F6", // blue
  "#06B6D4", // cyan
  "#22C55E", // green
  "#F59E0B", // amber
  "#EF4444", // red
  "#EC4899", // pink
  "#94A3B8", // slate
];

/** Deterministic color from tag name via hash. */
export function hashColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}

/** Background for a given hex color with mode-aware opacity. */
export function tagBg(hex, isDark = false) {
  if (!hex || hex.length < 7)
    return isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)";
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${isDark ? 0.16 : 0.08})`;
}

/**
 * Normalize a tag to { name, color } format.
 * Handles both legacy strings and rich objects.
 */
export function normalizeTag(tag) {
  if (typeof tag === "string") {
    return { name: tag, color: hashColor(tag) };
  }
  if (tag && typeof tag === "object" && tag.name) {
    return { name: tag.name, color: tag.color || hashColor(tag.name) };
  }
  return { name: String(tag ?? ""), color: TAG_COLORS[0] };
}

/** Normalize an array of tags. */
export function normalizeTags(tags) {
  if (typeof tags === "string") {
    const trimmed = tags.trim();
    if (!trimmed) return [];
    try {
      const parsed = JSON.parse(trimmed);
      return Array.isArray(parsed)
        ? parsed.map(normalizeTag)
        : [normalizeTag(parsed)];
    } catch {
      return [normalizeTag(trimmed)];
    }
  }
  if (!Array.isArray(tags)) return [];
  return tags.map(normalizeTag);
}

/** Get the display name of a tag (handles both formats). */
export function tagName(tag) {
  return typeof tag === "string" ? tag : tag?.name || "";
}
