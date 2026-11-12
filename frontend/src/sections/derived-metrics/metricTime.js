const metricDate = (value) => {
  if (value == null || value === "" || typeof value === "boolean") return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
};

/** Native datetime-local values are local. Saved instants are always labeled UTC. */
export const localTime = (value) => {
  const date = metricDate(value);
  if (!date) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};

/** utcSuffix: false is only for a column whose header already says UTC. */
export function formatMetricTime(value, { utcSuffix = true } = {}) {
  const date = metricDate(value);
  if (!date) return "Unavailable";
  return (
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "UTC",
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).format(date) + (utcSuffix === false ? "" : " UTC")
  );
}
/** A range of two instants for a column whose header says UTC. When both
 * fall on the same UTC day the date is shown once:
 * "11 Nov 2026, 09:49:47 – 17:49:47". */
export function formatMetricTimeRange(start, end) {
  const from = metricDate(start);
  const to = metricDate(end);
  const full = (value) => formatMetricTime(value, { utcSuffix: false });
  if (!from || !to) return `${full(start)} – ${full(end)}`;
  if (from.toISOString().slice(0, 10) !== to.toISOString().slice(0, 10))
    return `${full(from)} – ${full(to)}`;
  const clock = new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(to);
  return `${full(from)} – ${clock}`;
}
/** A time of day in UTC, for an action the viewer has just taken. */
export function formatMetricClock(value) {
  const date = metricDate(value);
  if (!date) return "an unknown time";
  return `${new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date)} UTC`;
}
export const localTimeZone = () =>
  Intl.DateTimeFormat().resolvedOptions().timeZone;
export const formatMetricWindow = (window) =>
  `${formatMetricTime(window?.window_start)} to ${formatMetricTime(window?.window_end)}`;

export function formatLocalMetricWindow(window) {
  const format = (value) => {
    const date = metricDate(value);
    if (!date) return "Unavailable";
    return new Intl.DateTimeFormat(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short",
    }).format(date);
  };
  return `${format(window?.window_start)} to ${format(window?.window_end)} (local time)`;
}

/** Closed elapsed-time range, computed once per selection (not on every render).
 * Keep the server's effective_window separately; it can be rounded to 15 minutes.
 */
export function metricWindow(days, now = new Date()) {
  const end = metricDate(now);
  if (!end || !Number.isInteger(days) || days < 1 || days > 90) {
    throw new Error("Choose a metric window between 1 and 90 days.");
  }
  return {
    window_start: new Date(end.getTime() - days * 86400000).toISOString(),
    window_end: end.toISOString(),
  };
}

/** A metric that started with no history (history_mode "forward") has values
 * only from its coverage_start. Reads start there instead of asking for a
 * window that was never calculated. A window wholly before coverage_start is
 * kept as chosen, so the server can say it is not calculated.
 */
export function coverageWindow(window, definition) {
  if (definition?.history_mode !== "forward") return window;
  const from = metricDate(definition.coverage_start);
  const start = metricDate(window?.window_start);
  const end = metricDate(window?.window_end);
  if (!from || !start || start >= from || (end && from >= end)) return window;
  return { ...window, window_start: from.toISOString() };
}

const BUCKET_MS = 15 * 60000;

/** The window the server reads or saves for a request: both ends rounded down
 * to a 15-minute UTC bucket, like its effective_window. An end that is not a
 * valid instant stays null; it is never replaced by a guess.
 */
export function metricBucketWindow(window) {
  const floor = (value) => {
    const date = metricDate(value);
    if (!date) return null;
    const bucket = Math.floor(date.getTime() / BUCKET_MS) * BUCKET_MS;
    return new Date(bucket).toISOString();
  };
  return {
    window_start: floor(window?.window_start),
    window_end: floor(window?.window_end),
  };
}

/** Pass provenance.updated_at, never definition.updated_at, for read freshness.
 * Future instants remain future (clock skew), not an invented 'just updated'.
 */
export function formatRelativeMetricTime(
  value,
  { now = new Date(), locale } = {},
) {
  const date = metricDate(value);
  const reference = metricDate(now);
  if (!date || !reference) return "Unavailable";
  const seconds = (date.getTime() - reference.getTime()) / 1000;
  const [unit, divisor] =
    Math.abs(seconds) >= 86400
      ? ["day", 86400]
      : Math.abs(seconds) >= 3600
        ? ["hour", 3600]
        : Math.abs(seconds) >= 60
          ? ["minute", 60]
          : ["second", 1];
  return new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(
    Math.trunc(seconds / divisor) || 0,
    unit,
  );
}
