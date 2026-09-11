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

export function toTimeRangePayload(range) {
  if (!range?.start || !range?.end) return null;
  return { custom_start: range.start, custom_end: range.end };
}
