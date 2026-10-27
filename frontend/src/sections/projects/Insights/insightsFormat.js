// Number and time formatting for Insights. Every number on a report goes
// through here so the same quantity reads the same everywhere.

const NUMBER = new Intl.NumberFormat("en-US");

export const formatCount = (value) =>
  Number.isFinite(value) ? NUMBER.format(Math.round(value)) : "–";

// 0.384 -> "38%". A share that rounds to 0 but is not zero reads "<1%", so a
// real category never looks empty.
export function formatPercent(value) {
  if (!Number.isFinite(value)) return "–";
  const pct = value * 100;
  if (pct > 0 && pct < 0.5) return "<1%";
  if (pct < 100 && pct > 99.5) return ">99%";
  return `${Math.round(pct)}%`;
}

// Latency in milliseconds -> "1.9 s"; sub-second -> "640 ms".
export function formatLatency(ms) {
  if (!Number.isFinite(ms)) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  return seconds >= 100
    ? `${Math.round(seconds)} s`
    : `${seconds.toFixed(1)} s`;
}

export function formatCost(usd) {
  if (!Number.isFinite(usd)) return "–";
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

// Sentiment is a 1–5 score.
export const formatSentiment = (value) =>
  Number.isFinite(value) ? value.toFixed(1) : "–";

export const formatTotal = (total, isLowerBound) =>
  `${formatCount(total)}${isLowerBound ? "+" : ""}`;

const toDate = (value) => {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

export function relativeTime(value, now = new Date()) {
  const date = toDate(value);
  if (!date) return "";
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return formatDay(date);
}

export function formatDay(value) {
  const date = toDate(value);
  return date
    ? date.toLocaleDateString("en-US", { month: "short", day: "numeric" })
    : "";
}

// "Oct 15, 10:12 AM"
export function formatWhen(value) {
  const date = toDate(value);
  return date
    ? date.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "";
}

// 165 -> "2m 45s"
export function formatSpan(totalSeconds) {
  if (!Number.isFinite(totalSeconds)) return "";
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

export function formatDuration(from, to) {
  const start = toDate(from);
  const end = toDate(to);
  if (!start || !end) return "";
  return formatSpan((end - start) / 1000);
}

// "about 3 minutes": roughly a minute per hundred units, plus the grouping and
// writing that follow; never promise less than two.
export function estimateMinutes(units) {
  const minutes = Math.max(2, Math.ceil((units || 0) / 100) + 1);
  return `about ${minutes} minutes`;
}

// "asked_for_human" -> "Asked for human"
export function humanizeKey(key) {
  const text = String(key || "")
    .replace(/^attr:/, "")
    .replace(/_/g, " ")
    .trim();
  return text ? text[0].toUpperCase() + text.slice(1) : "";
}

// "partially_resolved" -> "Partially resolved"
export const humanizeValue = (value) => humanizeKey(value);

// "Oct 8–15", "Sep 28 – Oct 3", "Oct 8"; a year is added when the range is
// not in the current one.
export function formatDayRange(from, to, now = new Date()) {
  const start = toDate(from);
  const end = toDate(to) || start;
  if (!start) return "";
  const sameYear = start.getFullYear() === end.getFullYear();
  const withYear = !sameYear || start.getFullYear() !== now.getFullYear();
  const month = (date) => date.toLocaleDateString("en-US", { month: "short" });
  const year = withYear ? `, ${end.getFullYear()}` : "";
  if (
    start.getFullYear() === end.getFullYear() &&
    start.getMonth() === end.getMonth()
  ) {
    if (start.getDate() === end.getDate())
      return `${month(start)} ${start.getDate()}${year}`;
    return `${month(start)} ${start.getDate()}–${end.getDate()}${year}`;
  }
  const startYear = sameYear ? "" : `, ${start.getFullYear()}`;
  return `${month(start)} ${start.getDate()}${startYear} – ${month(end)} ${end.getDate()}${year}`;
}
