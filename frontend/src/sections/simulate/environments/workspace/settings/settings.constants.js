export const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

export const KEY_W = 220;

export const DIVIDER_SX = { borderBottom: "1px solid", borderColor: "divider" };

// The outlined kind chip on an env-var or credential-file row.
export const KIND_CHIP_SX = {
  height: 19,
  borderRadius: 0.5,
  flexShrink: 0,
  color: "text.secondary",
  border: "1px solid",
  borderColor: "divider",
  bgcolor: "transparent",
  "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
};

export const SECRET_MASK = "••••••••••••";

// 3600 → "60 minutes", 7200 → "2 hours", 45 → "45 seconds". An hour reads as
// "60 minutes" as the Contract tab words the same limit.
export const durationWords = (sec) => {
  if (sec % 60 !== 0) return `${sec} seconds`;
  const min = sec / 60;
  if (min <= 60) return `${min} minute${min === 1 ? "" : "s"}`;
  const h = min / 60;
  return Number.isInteger(h) ? `${h} hours` : `${min} minutes`;
};

// "dedicated_vm" → "Dedicated VM".
const WORD = { vm: "VM", cpu: "CPU", gpu: "GPU" };
export const humanize = (value) =>
  String(value)
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map(
      (w, i) =>
        WORD[w.toLowerCase()] ||
        (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w.toLowerCase()),
    )
    .join(" ");
