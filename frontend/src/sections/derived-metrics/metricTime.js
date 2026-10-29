/** Native datetime-local values are local. Saved instants are always labeled UTC. */
export const localTime = (date) =>
  new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);

export function formatMetricTime(value) {
  if (!value) return "Unavailable";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Unavailable";
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
export const formatMetricWindow = (run) =>
  `${formatMetricTime(run.window_start)} to ${formatMetricTime(run.window_end)}`;

export function formatLocalMetricWindow(run) {
  const format = (value) => {
    const date = new Date(value);
    if (!value || !Number.isFinite(date.getTime())) return "Unavailable";
    return new Intl.DateTimeFormat(undefined, {
      day: "numeric", month: "short", year: "numeric",
      hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short",
    }).format(date);
  };
  return `${format(run.window_start)} to ${format(run.window_end)} (local time)`;
}
