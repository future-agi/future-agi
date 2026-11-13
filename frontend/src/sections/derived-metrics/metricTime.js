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

const ceilBucket = (date) =>
  new Date(Math.ceil(date.getTime() / BUCKET_MS) * BUCKET_MS);

/** The first 15-minute UTC boundary at or after an instant. A metric that
 * started with no history computes only units starting at or after its
 * coverage_start, so the bucket holding coverage_start is partial: its
 * values begin here, the earliest window start the server calls calculated.
 */
export function firstValuesInstant(coverageStart) {
  const coverage = metricDate(coverageStart);
  return coverage ? ceilBucket(coverage) : null;
}

/** firstValuesInstant of a metric that started with no history
 * (history_mode "forward"); null for any other metric. */
export function forwardValuesStart(definition) {
  if (definition?.history_mode !== "forward") return null;
  return firstValuesInstant(definition.coverage_start);
}

/** The window to read for a metric that started with no history: it begins
 * where values begin instead of asking for a window that was never calculated.
 * A window already starting there, or wholly before coverage_start, stays as
 * chosen (the server then says it is not calculated). */
export function coverageWindow(window, definition) {
  const first = forwardValuesStart(definition);
  if (!first) return window;
  const start = metricDate(window?.window_start);
  const end = metricDate(window?.window_end);
  const coverage = metricDate(definition.coverage_start);
  if (!start || start >= first || (end && end <= coverage)) return window;
  return { ...window, window_start: first.toISOString() };
}

/** True while the window reaches past coverage_start but holds no full
 * 15-minute bucket of values yet, which the server cannot read (it floors
 * both ends to a bucket and needs at least one bucket between them). No read
 * is sent; the page says when the first values appear. */
export function coveragePending(window, definition) {
  const first = forwardValuesStart(definition);
  const end = metricDate(window?.window_end);
  if (!first || !end) return false;
  // The effective-window reader floors both ends. Until window_end reaches
  // the end of the first whole bucket, a window clamped to `first` would be
  // empty (activation can happen just after its mount-time end).
  return end < new Date(first.getTime() + BUCKET_MS);
}

/** When the first bucket of values ends: a preset window ending before this
 * instant is pending, or stale if its metric arrived after the deadline. */
export function coverageReadableAt(window, definition) {
  const first = forwardValuesStart(definition);
  const end = metricDate(window?.window_end);
  const readable = first && new Date(first.getTime() + BUCKET_MS);
  // Also return a past deadline for a stale preset window whose metric arrived
  // after the first bucket had already completed; the hook re-anchors it now.
  return readable && end && end < readable ? readable : null;
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
