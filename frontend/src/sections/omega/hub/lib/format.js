// Display helpers. A count the server has not measured shows "—", never 0
// presented as data (SPEC §6.2, the never-invent-a-number pattern).

export function countOrDash(value) {
  if (value === null || value === undefined || Number(value) <= 0) return "—";
  return compactNumber(Number(value));
}

// A count the server measures exactly (likes): 0 is a real value.
export function exactCount(value) {
  return compactNumber(Math.max(0, Number(value) || 0));
}

// A count worth showing on a card, a button or a list row: a number above
// zero, compacted, or null. A new Hub then shows no "♡ 0", "⬇ —" or
// "— installs" that reads as broken; the count appears once there is one.
export function countIfAny(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? compactNumber(number) : null;
}

export function compactNumber(value) {
  if (value < 1000) return String(value);
  if (value < 1_000_000)
    return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

export function formatDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timeAgo(iso, now = Date.now()) {
  if (!iso) return "";
  const seconds = Math.max(
    0,
    Math.round((now - new Date(iso).getTime()) / 1000),
  );
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function shortDigest(digest, length = 8) {
  if (!digest) return "—";
  const [algo, hex] = digest.includes(":")
    ? digest.split(":")
    : ["sha256", digest];
  return `${algo}:${hex.slice(0, length)}`;
}

export function splitRef(ref) {
  const [path, version] = String(ref || "").split("@");
  const [publisher, name] = path.split("/");
  return { publisher, name, version: version || null };
}

// Monday of the current UTC week, as YYYY-MM-DD (picks are per week).
export function mondayOf(date = new Date()) {
  const d = new Date(
    Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()),
  );
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() - day + 1);
  return d.toISOString().slice(0, 10);
}
