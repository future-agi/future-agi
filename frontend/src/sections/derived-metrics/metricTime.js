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

export function formatMetricTime(value) {
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
    }).format(date) + " UTC"
  );
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
