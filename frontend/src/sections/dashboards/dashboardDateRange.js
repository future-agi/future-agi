// Date-range helpers for the dashboard date filter and the widget editor.
// `reference` is injectable so the logic is deterministic under test.
import { isValid } from "date-fns";
import { DashboardTimeRangeApiPreset } from "src/generated/api-contracts/api.schemas";

export const DEFAULT_DATE_PRESET = "30D";

// Unknown presets fall back to the default so an off-enum value can't reach the API.
const VALID_PRESETS = new Set(Object.values(DashboardTimeRangeApiPreset));
const normalizePreset = (preset) =>
  VALID_PRESETS.has(preset) ? preset : DEFAULT_DATE_PRESET;

const toISOIfValid = (value) => {
  if (value == null) return null;
  const d = value instanceof Date ? value : new Date(value);
  return isValid(d) ? d.toISOString() : null;
};

export function getDateRange(preset, reference = new Date()) {
  const now = new Date(reference);
  const start = new Date(reference);
  switch (preset) {
    case "30m":
      start.setMinutes(start.getMinutes() - 30);
      break;
    case "6h":
      start.setHours(start.getHours() - 6);
      break;
    case "today":
      start.setHours(0, 0, 0, 0);
      break;
    case "yesterday":
      start.setDate(start.getDate() - 1);
      start.setHours(0, 0, 0, 0);
      now.setDate(now.getDate() - 1);
      now.setHours(23, 59, 59, 999);
      break;
    case "7D":
      start.setDate(start.getDate() - 7);
      break;
    case "30D":
      start.setDate(start.getDate() - 30);
      break;
    case "3M":
      start.setMonth(start.getMonth() - 3);
      break;
    case "6M":
      start.setMonth(start.getMonth() - 6);
      break;
    case "12M":
      start.setMonth(start.getMonth() - 12);
      break;
    default:
      return null;
  }
  return { start: start.toISOString(), end: now.toISOString() };
}

// Resolve the global date override applied to every widget query:
// a custom [start, end] pair when the user picked one, otherwise the preset
// range, or null ("Default" — each widget keeps its own stored time range).
export function resolveGlobalDateRange(
  datePreset,
  customDateRange,
  reference = new Date(),
) {
  if (datePreset === "custom") {
    const start = toISOIfValid(customDateRange?.[0]);
    const end = toISOIfValid(customDateRange?.[1]);
    return start && end ? { start, end } : null;
  }
  return datePreset ? getDateRange(datePreset, reference) : null;
}

export function buildTimeRangePayload(preset, customDateRange) {
  if (preset === "custom" && customDateRange) {
    const start = toISOIfValid(customDateRange[0]);
    const end = toISOIfValid(customDateRange[1]);
    if (start && end) return { custom_start: start, custom_end: end };
  }
  return { preset: normalizePreset(preset) };
}

export function resolveInitialTimeRange(savedTimeRange, urlPreset) {
  const tr = savedTimeRange || {};
  if (!urlPreset && tr.custom_start && tr.custom_end) {
    const start = new Date(tr.custom_start);
    const end = new Date(tr.custom_end);
    if (isValid(start) && isValid(end)) {
      return { timePreset: "custom", customDateRange: [start, end] };
    }
  }
  return {
    timePreset: urlPreset || tr.preset || DEFAULT_DATE_PRESET,
    customDateRange: null,
  };
}

// Dashboard Date/epoch-millisecond inputs retain their existing precision. Wire
// timestamps, unlike Date, carry Python/DRF's microseconds (extra digits truncate,
// never round). Do not use the span-reference parser: it accepts only zoned
// six-digit strings, whereas dashboard inputs also include Dates and date-only
// values. BigInt avoids losing precision for dates outside Number's safe us range.
const epochMicroseconds = (value) => {
  let normalized = value;
  let subMillisecond = 0n;
  if (typeof value === "string") {
    const match = value.match(
      /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?(Z|[+-]\d{2}(?::?\d{2})?)?)?$/,
    );
    if (!match) return null;
    const [, year, month, day, hour, minute, second, fraction = "", zone = ""] =
      match;
    const leap = +year % 4 === 0 && (+year % 100 !== 0 || +year % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (
      +year < 1 ||
      +month < 1 ||
      +month > 12 ||
      +day < 1 ||
      +day > days[+month - 1] ||
      +hour > 23 ||
      +minute > 59 ||
      +second > 59
    )
      return null;
    if (hour != null) {
      let offset = zone;
      if (/^[+-]\d{2}$/.test(offset)) offset += ":00";
      if (/^[+-]\d{4}$/.test(offset))
        offset = `${offset.slice(0, 3)}:${offset.slice(3)}`;
      if (
        offset &&
        offset !== "Z" &&
        (+offset.slice(1, 3) > 23 || +offset.slice(4) > 59)
      )
        return null;
      const micros = fraction.padEnd(6, "0").slice(0, 6);
      normalized = `${year}-${month}-${day}T${hour}:${minute}:${second || "00"}.${micros.slice(0, 3)}${offset}`;
      subMillisecond = BigInt(micros.slice(3));
    }
  } else if (!(value instanceof Date) && typeof value !== "number") {
    return null;
  }
  const milliseconds = new Date(normalized).getTime();
  return Number.isFinite(milliseconds)
    ? BigInt(milliseconds) * 1000n + subMillisecond
    : null;
};

/** A saved run contributes one observation at window_end, included at BOTH boundaries.
 * Missing dates, mixed metrics and unavailable reads are not evidence of exclusion.
 */
export function isPinnedWindowExcluded(config, range, savedRun) {
  if (!range || config?.metrics?.length !== 1) return false;
  const metric = config.metrics[0];
  if (metric.type !== "derived_metric" || !metric.derived_run_id) return false;
  if (savedRun && savedRun.id !== metric.derived_run_id) return false;
  const dates = [
    savedRun ? savedRun.window_start : config.time_range?.custom_start,
    savedRun ? savedRun.window_end : config.time_range?.custom_end,
    range.start,
    range.end,
  ];
  if (dates.some((value) => value == null || value === "")) return false;
  const [savedStart, savedEnd, start, end] = dates.map(epochMicroseconds);
  if (
    [savedStart, savedEnd, start, end].some((value) => value == null) ||
    savedStart >= savedEnd ||
    start > end
  )
    return false;
  return savedEnd < start || savedEnd > end;
}

export function toTimeRangePayload(range) {
  if (!range?.start || !range?.end) return null;
  return { custom_start: range.start, custom_end: range.end };
}
