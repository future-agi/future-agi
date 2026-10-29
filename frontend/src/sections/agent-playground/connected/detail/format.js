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
  rate >= 80 ? "#16A34A" : rate >= 50 ? "text.primary" : "#DC2626";
