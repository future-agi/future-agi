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
  // A URL preset carries no dates, so only a real preset may override the
  // saved range; "custom" (or anything off-enum) keeps the widget's saved one.
  const override = VALID_PRESETS.has(urlPreset) ? urlPreset : null;
  if (!override && tr.custom_start && tr.custom_end) {
    const start = new Date(tr.custom_start);
    const end = new Date(tr.custom_end);
    if (isValid(start) && isValid(end)) {
      return { timePreset: "custom", customDateRange: [start, end] };
    }
  }
  return {
    timePreset: override || tr.preset || DEFAULT_DATE_PRESET,
    customDateRange: null,
  };
}

// The dashboard date filter rides the URL between the dashboard and the widget
// editor, which are separate routes. A Custom choice carries its dates too:
// "custom" alone restored the chip without its range, so returning from the
// editor dropped the override and a new widget saved the 30D default.
const CUSTOM_START_PARAM = "customStart";
const CUSTOM_END_PARAM = "customEnd";

export function toDateFilterSearch(datePreset, customDateRange) {
  const params = new URLSearchParams();
  if (!datePreset) return params;
  params.set("timePreset", datePreset);
  if (datePreset === "custom") {
    const start = toISOIfValid(customDateRange?.[0]);
    const end = toISOIfValid(customDateRange?.[1]);
    if (start && end) {
      params.set(CUSTOM_START_PARAM, start);
      params.set(CUSTOM_END_PARAM, end);
    }
  }
  return params;
}

// Append the date filter to `path`, after any params `path` already carries.
export function withDateFilterSearch(path, datePreset, customDateRange) {
  const search = toDateFilterSearch(datePreset, customDateRange).toString();
  if (!search) return path;
  return `${path}${path.includes("?") ? "&" : "?"}${search}`;
}

export function readCustomDateRange(searchParams) {
  if (searchParams?.get("timePreset") !== "custom") return null;
  const start = toISOIfValid(searchParams.get(CUSTOM_START_PARAM));
  const end = toISOIfValid(searchParams.get(CUSTOM_END_PARAM));
  return start && end ? [new Date(start), new Date(end)] : null;
}

export function toTimeRangePayload(range) {
  if (!range?.start || !range?.end) return null;
  return { custom_start: range.start, custom_end: range.end };
}
