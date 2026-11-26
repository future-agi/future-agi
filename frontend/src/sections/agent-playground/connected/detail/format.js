// Formatting shared by the agent detail tabs.

export const MONO = "ui-monospace, Menlo, monospace";

export const ago = (iso) => {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d}d ago`;
};

export const rateTone = (rate) =>
  rate >= 80
    ? "status.success.fg"
    : rate >= 50
      ? "text.primary"
      : "status.danger.fg";

export const syncedText = (checkedAt) =>
  checkedAt ? `Synced ${ago(checkedAt)}` : "Not synced yet";

export function syncTooltip(intervalSeconds) {
  const minutes = Math.round(intervalSeconds / 60);
  return `New commits arrive on push; we also check GitHub every ${minutes} minutes. Sync checks now.`;
}
