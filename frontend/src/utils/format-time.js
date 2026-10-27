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

let displayTimeZone;

function validTimeZone(timeZone) {
  if (typeof timeZone !== "string" || !timeZone) return "UTC";
  try {
    new Intl.DateTimeFormat("en-US", { timeZone });
    return timeZone;
  } catch {
    return "UTC";
  }
}

// Resolve once for this page load, independently of the formatting locale.
export function getDisplayTimeZone() {
  if (displayTimeZone === undefined) {
    try {
      displayTimeZone = validTimeZone(
        Intl.DateTimeFormat().resolvedOptions().timeZone,
      );
    } catch {
      displayTimeZone = "UTC";
    }
  }
  return displayTimeZone;
}

function toInstant(value) {
  if (typeof value === "string") {
    // Calendar dates and timestamps without an explicit zone are not instants.
    const match = value.match(
      /^(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$/,
    );
    if (!match) return null;
    const [year, month, day] = match.slice(1, 4).map(Number);
    const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const daysInMonth = [
      31,
      leapYear ? 29 : 28,
      31,
      30,
      31,
      30,
      31,
      31,
      30,
      31,
      30,
      31,
    ];
    // Date otherwise silently normalizes invalid days such as 30 February.
    if (month < 1 || month > 12 || day < 1 || day > daysInMonth[month - 1]) {
      return null;
    }
  } else if (!(value instanceof Date)) {
    // Bare numbers are not instants. new Date(0) is 1970 and new Date(1) is a
    // fabricated date; the API sends ISO strings, never epoch milliseconds.
    return null;
  }
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

function formatLocalInstant(value, { timeZone } = {}) {
  const parsed = toInstant(value);
  if (!parsed) return null;
  const zone =
    timeZone === undefined ? getDisplayTimeZone() : validTimeZone(timeZone);
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZoneName: "longOffset",
    })
      .formatToParts(parsed)
      .map(({ type, value: part }) => [type, part]),
  );
  const date = `${parts.day} ${parts.month} ${parts.year}`;
  return {
    date,
    local: `${date}, ${parts.hour}:${parts.minute} ${parts.dayPeriod}`,
    zone,
    offset:
      parts.timeZoneName === "GMT"
        ? "UTC+00:00"
        : parts.timeZoneName.replace("GMT", "UTC"),
    utc: parsed.toISOString(),
  };
}

export function fDateLocal(value, options) {
  return formatLocalInstant(value, options)?.date ?? "";
}

export function fDateTimeLocal(value, options) {
  return formatLocalInstant(value, options)?.local ?? "";
}

export function describeInstant(value, options) {
  const formatted = formatLocalInstant(value, options);
  if (!formatted) return null;
  const { local, zone, offset, utc } = formatted;
  return { local, zone, offset, utc };
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
