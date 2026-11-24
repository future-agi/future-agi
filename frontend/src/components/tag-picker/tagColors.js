/* eslint-disable no-restricted-syntax -- ds: data colours (user-pickable tag colours) */
const TAG_COLORS = [
  "#EF4444",
  "#3B82F6",
  "#F59E0B",
  "#22C55E",
  "#7857FC",
  "#EC4899",
  "#94A3B8",
  "#06B6D4",
];

export function getTagColor(tag) {
  let hash = 0;
  for (let i = 0; i < tag.length; i++)
    hash = tag.charCodeAt(i) + ((hash << 5) - hash);
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}
