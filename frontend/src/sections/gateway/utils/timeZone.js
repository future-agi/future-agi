// Times on gateway charts, in one zone: the viewer's, as Logs states it.
// ApexCharts labels a datetime axis, and formats its tooltip, in UTC unless
// told otherwise, so every gateway datetime axis and tooltip is built here.
//
// Day buckets are days in a zone. Every gateway time series asks the API for
// the viewer's (`timeZoneParam`) and the API names the zone it cut its days
// in (`time_zone`): the viewer's, or UTC when it could not use theirs. A day
// bucket reads in that zone (`bucketTime`), so a bar is never labelled the
// day before its own.

import { EMPTY } from "./formatters";

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

const pad = (n) => String(n).padStart(2, "0");

const UTC = "UTC";

// The browser's IANA zone name, or null when it names none.
function browserZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

/** The viewer's zone, as gateway pages name it next to times. */
export function localZoneName() {
  return browserZone() || "local time";
}

/** `{ tz }` for a time-series request: the API cuts day buckets in it. */
export function timeZoneParam() {
  const zone = browserZone();
  return zone ? { tz: zone } : {};
}

/**
 * How a series' buckets read: `day` for day buckets; `utc` when those are
 * UTC days (the API named no zone, or UTC) and the viewer is not on UTC.
 * Minute and hour buckets read by instant, in the viewer's zone.
 */
export function bucketTime(granularity, data) {
  const day = granularity === "day";
  const zone = data?.time_zone || UTC;
  return { day, utc: day && zone === UTC && browserZone() !== UTC };
}

/** Said under a day chart whose bars are UTC days. */
export const UTC_DAYS_NOTE = "Daily bars are UTC days.";

/** The sentence pages put under a time chart or table. */
export const zoneNote = ({ utcDays = false } = {}) =>
  `Times are in ${localZoneName()}.${utcDays ? ` ${UTC_DAYS_NOTE}` : ""}`;

/**
 * "Oct 21, 23:39" in the viewer's zone ("Oct 21" for day buckets); in UTC
 * with `utc` (a UTC day bucket).
 */
export function formatChartTime(value, { day = false, utc = false } = {}) {
  if (value === null || value === undefined || value === "") return EMPTY;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return EMPTY;
  const month = utc ? date.getUTCMonth() : date.getMonth();
  const dayOfMonth = utc ? date.getUTCDate() : date.getDate();
  const dayText = `${MONTHS[month]} ${dayOfMonth}`;
  if (day) return dayText;
  const hours = utc ? date.getUTCHours() : date.getHours();
  const minutes = utc ? date.getUTCMinutes() : date.getMinutes();
  return `${dayText}, ${pad(hours)}:${pad(minutes)}`;
}

/**
 * A datetime x-axis labelled in the viewer's zone, or in UTC for UTC day
 * buckets (`utc`, from bucketTime).
 */
export function localTimeAxis({
  color,
  fontSize = "11px",
  utc = false,
  ...rest
} = {}) {
  return {
    ...rest,
    type: "datetime",
    labels: {
      datetimeUTC: utc,
      ...(color && { style: { colors: color, fontSize } }),
    },
  };
}

/** A tooltip's x part: the same time as the axis (`{ day, utc }`). */
export function localTimeTooltip({ day = false, utc = false } = {}) {
  return { formatter: (value) => formatChartTime(value, { day, utc }) };
}
