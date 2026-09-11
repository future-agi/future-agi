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
