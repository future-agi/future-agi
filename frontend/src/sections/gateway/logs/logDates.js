// Times on the Logs page. The filter inputs are <input type="datetime-local">,
// which carries no zone: a picked value is the viewer's local time. It is
// sent to the API (and kept in the URL) as an ISO instant, so the filter and
// the table, which shows local times, agree.

import { EMPTY } from "../utils/formatters";
import { localZoneName } from "../utils/timeZone";

const pad = (n, width = 2) => String(n).padStart(width, "0");

const valid = (date) => date instanceof Date && !Number.isNaN(date.getTime());

/** A datetime-local value (local time) as an ISO instant; "" when empty or invalid. */
export function localInputToIso(local) {
  if (!local) return "";
  const date = new Date(local);
  return valid(date) ? date.toISOString() : "";
}

// A time with no zone: a date, with or without a time of day.
const ZONELESS = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$/;

/**
 * A filter time from the URL as a Date. A zone-less value (an old bookmark
 * or a hand-built link, also a bare date) is the viewer's local time; null
 * when it cannot be read.
 */
export function parseFilterTime(value) {
  if (!value) return null;
  const text = String(value);
  const date = new Date(
    ZONELESS.test(text) && !text.includes("T") ? `${text}T00:00` : text,
  );
  return valid(date) ? date : null;
}

/**
 * A filter time as the API is sent it. The API reads a zone-less time as
 * UTC, so a zone-less value goes as the instant the page shows (local time);
 * a zoned value, or one that cannot be read, goes as it is.
 */
export function toApiInstant(value) {
  if (typeof value !== "string" || !ZONELESS.test(value)) return value;
  const date = parseFilterTime(value);
  return date ? date.toISOString() : value;
}

/** An ISO instant (or an old zone-less value, read as local) as a datetime-local value. */
export function toLocalInput(value) {
  const date = parseFilterTime(value);
  if (!date) return "";
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

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

/** "Oct 20 15:40:05.514" in local time, as dense log tables show it. */
export function formatLogTime(value) {
  if (!value) return EMPTY;
  const date = new Date(value);
  if (!valid(date)) return EMPTY;
  return (
    `${MONTHS[date.getMonth()]} ${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
    `.${pad(date.getMilliseconds(), 3)}`
  );
}

// The viewer's zone: one helper for Logs and the charts (utils/timeZone.js).
export { localZoneName } from "../utils/timeZone";

const AGE_UNITS = [
  ["day", 24 * 60 * 60],
  ["hour", 60 * 60],
  ["minute", 60],
];

/** "3 hours ago" (whole units, rounded down), "just now" under a minute; "" when unreadable. */
export function relativeLogTime(value, now = Date.now()) {
  const date = value ? new Date(value) : null;
  if (!valid(date)) return "";
  const seconds = Math.floor((now - date.getTime()) / 1000);
  if (Math.abs(seconds) < 60) return "just now";
  const format = new Intl.RelativeTimeFormat("en", { numeric: "always" });
  const [unit, size] =
    AGE_UNITS.find(([, s]) => Math.abs(seconds) >= s) || AGE_UNITS.at(-1);
  const count = Math.floor(Math.abs(seconds) / size);
  return format.format(seconds < 0 ? count : -count, unit);
}

/**
 * "Nov 11, 2026 02:54:31.370 America/Los_Angeles, 3 hours ago": a log time's
 * exact local instant with the zone and its age, for hover titles.
 */
export function exactLogTime(value, now = Date.now()) {
  const date = value ? new Date(value) : null;
  if (!valid(date)) return "";
  const stamp =
    `${MONTHS[date.getMonth()]} ${pad(date.getDate())}, ${date.getFullYear()} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
    `.${pad(date.getMilliseconds(), 3)}`;
  return `${stamp} ${localZoneName()}, ${relativeLogTime(value, now)}`;
}
