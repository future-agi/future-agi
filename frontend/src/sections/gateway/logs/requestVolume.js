// The Logs page's request volume: the window it covers and the bucket size.
// It reads GET /agentcc/request-logs/volume/ (agentcc/views/request_log.py
// volume), which applies the list's own filters, search included.

import { formatLogTime, parseFilterTime } from "./logDates";

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

const UNIT = { minute: "minute", hour: "hour", day: "day" };

/**
 * The window and bucket size for the logs' time filters, the table's own
 * window: with no start time, from the oldest request that matches the
 * table (`oldest`) to the end; the last 24 hours only while that is not
 * known. Up to now when no end is set. Buckets: by minute up to 6 hours, by
 * hour up to 7 days, else by day (the API's own sizes).
 */
export function volumeWindow(filters = {}, now = new Date(), oldest = null) {
  const after = parseFilterTime(filters.startedAfter);
  const before = parseFilterTime(filters.startedBefore);
  const end = before || now;
  const first = after ? null : parseFilterTime(oldest);
  const start = after || first || new Date(end.getTime() - DAY);
  const span = Math.abs(end.getTime() - start.getTime());
  const granularity =
    span <= 6 * HOUR ? "minute" : span <= 7 * DAY ? "hour" : "day";
  return {
    start: start.toISOString(),
    end: end.toISOString(),
    granularity,
    since: Boolean(first),
    openEnd: !before,
    lastDay: !after && !before && !first,
  };
}

// A window end as the From / To chips write it: to the second.
const toSecond = (value) => formatLogTime(value).replace(/\.\d{3}$/, "");

/**
 * "Requests per hour, last 24 hours", "..., since <first request>" (no time
 * filter: the table's whole window) or "..., <from> to <to>".
 */
export function volumeTitle({
  start,
  end,
  granularity,
  lastDay,
  since,
  openEnd,
}) {
  const per = `Requests per ${UNIT[granularity] || granularity}`;
  if (lastDay) return `${per}, last 24 hours`;
  if (since && openEnd) return `${per}, since ${toSecond(start)}`;
  return `${per}, ${toSecond(start)} to ${toSecond(end)}`;
}

/** OK and error counts per bucket, and the totals. */
export function volumeSeries(points = []) {
  const ok = [];
  const errors = [];
  let requests = 0;
  let failed = 0;
  points.forEach((point) => {
    const x = Date.parse(point.bucket);
    const count = Number(point.request_count) || 0;
    const errorCount = Math.min(Number(point.error_count) || 0, count);
    requests += count;
    failed += errorCount;
    ok.push({ x, y: count - errorCount });
    errors.push({ x, y: errorCount });
  });
  return { ok, errors, requests, failed };
}
