import {
  format,
  getTime,
  formatDistanceToNow,
  formatDistanceToNowStrict,
} from "date-fns";

// ----------------------------------------------------------------------

// date-fns throws RangeError on an Invalid Date, which escapes as a render crash.
export function toValidDate(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

export function fDate(date, newFormat) {
  const fm = newFormat || "dd MMM yyyy";
  const parsed = toValidDate(date);

  return parsed ? format(parsed, fm) : "";
}

export function fDateTime(date, newFormat) {
  const fm = newFormat || "dd MMM yyyy p";
  const parsed = toValidDate(date);

  return parsed ? format(parsed, fm) : "";
}

export function fTimestamp(date) {
  const parsed = toValidDate(date);

  return parsed ? getTime(parsed) : "";
}

export function fToNow(date) {
  const parsed = toValidDate(date);

  return parsed
    ? formatDistanceToNow(parsed, {
        addSuffix: true,
      })
    : "";
}

export function fToNowStrict(date) {
  const parsed = toValidDate(date);

  return parsed
    ? formatDistanceToNowStrict(parsed, {
        addSuffix: true,
      })
    : "";
}

// Compact "3 hours ago" relative time for the environments table. Deliberately
// distinct from fToNow, which emits date-fns's "about 3 hours ago" phrasing.
export function relativeTime(iso) {
  const then = toValidDate(iso);
  if (!then) return "—";
  const diffMs = Date.now() - then.getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60)
    return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} ${days === 1 ? "day" : "days"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} ${months === 1 ? "month" : "months"} ago`;
  const years = Math.floor(months / 12);
  return `${years} ${years === 1 ? "year" : "years"} ago`;
}

export const formatDuration = (seconds) => {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = seconds % 60;

  let result = "";
  if (hours > 0) result += `${hours}h `;
  if (minutes > 0) result += `${minutes}m `;
  if (remainingSeconds > 0) result += `${remainingSeconds}s`;

  return result.trim() || "0s";
};
