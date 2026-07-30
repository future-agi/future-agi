export function parseTimeoutSeconds(value) {
  const text = String(value ?? "")
    .trim()
    .toLowerCase();
  if (!text) return null;

  const match = text.match(/^(\d+)(?:\s*(ms|s|m))?$/);
  if (!match) return null;

  const amount = Number(match[1]);
  if (!Number.isSafeInteger(amount) || amount <= 0) return null;

  const unit = match[2] || "s";
  if (unit === "ms") return Math.max(1, Math.ceil(amount / 1000));
  if (unit === "m") return amount * 60;
  return amount;
}
