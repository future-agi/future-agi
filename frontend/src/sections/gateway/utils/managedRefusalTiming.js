// Only call with response-header data (or a value already derived from it).
// Match the relay transport: decimal seconds or HTTP-date, bounded to one day.
const MAX_SECONDS = 86400;
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];
const IMF =
  /^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), ([0-9]{2}) ([A-Z][a-z]{2}) ([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$/;
const OBSOLETE =
  /^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), ([0-9]{2})-([A-Z][a-z]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$/;
const ASCTIME =
  /^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) ([ 0-9][0-9]) ([0-9]{2}):([0-9]{2}):([0-9]{2}) ([0-9]{4})$/;
const bounded = (value) =>
  Number.isSafeInteger(value) && value >= 0 && value <= MAX_SECONDS;

export function readRetryAfterSeconds(value) {
  if (typeof value === "number") return bounded(value) ? value : null;
  if (
    typeof value !== "string" ||
    value.length > 128 ||
    Array.from(value).some(
      (char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127,
    )
  )
    return null;
  const raw = value.trim();
  if (/^[0-9]+$/.test(raw)) {
    const seconds = Number(raw);
    return bounded(seconds) ? seconds : null;
  }
  // Do not feed arbitrary prose/ISO dates to Date.parse's permissive grammar.
  let parts = IMF.exec(raw) || OBSOLETE.exec(raw);
  if (!parts) {
    const old = ASCTIME.exec(raw);
    if (old) parts = [old[0], old[2], old[1], old[6], old[3], old[4], old[5]];
  }
  if (!parts) return null;
  const now = Date.now();
  const day = Number(parts[1]);
  const month = MONTHS.indexOf(parts[2]);
  let year = Number(parts[3]);
  if (parts[3].length === 2) {
    const currentYear = new Date(now).getUTCFullYear();
    year += Math.floor(currentYear / 100) * 100;
    if (year > currentYear + 50) year -= 100;
  }
  const [hour, minute, second] = parts.slice(4).map(Number);
  const target = new Date(Date.UTC(year, month, day, hour, minute, second));
  const delta = (target.getTime() - now) / 1000;
  if (
    month < 0 ||
    target.getUTCFullYear() !== year ||
    target.getUTCMonth() !== month ||
    target.getUTCDate() !== day ||
    target.getUTCHours() !== hour ||
    target.getUTCMinutes() !== minute ||
    target.getUTCSeconds() !== second ||
    delta < 0 ||
    delta > MAX_SECONDS
  )
    return null;
  const seconds = Math.ceil(delta);
  return bounded(seconds) ? seconds : null;
}
