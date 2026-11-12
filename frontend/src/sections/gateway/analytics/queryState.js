import { ANALYTICS_RANGES, DEFAULT_RANGE, rangeWindow } from "./analyticsRange";
import { REQUEST_DIMENSION_OPTIONS } from "../constants/requestTags";
import { COST_DIMENSION_OPTIONS } from "./costDimensions";

const DAY = 86400000;
export const MAX_ANALYTICS_WINDOW_MS = 1999 * DAY;
const DEFAULT_GROUPS = { usage: "", cost: "model", errors: "status_code" };
const DIMENSIONS = REQUEST_DIMENSION_OPTIONS.map(({ value }) => value);
const GROUPS = {
  usage: ["", ...DIMENSIONS],
  cost: COST_DIMENSION_OPTIONS.map(({ value }) => value),
  errors: ["status_code", ...DIMENSIONS],
};

// Links carry instants, never zone-less local dates or JS's normalized Feb 30.
function instant(value) {
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.exec(
      value || "",
    );
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match.map(Number);
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  if (
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > days ||
    hour > 23 ||
    minute > 59 ||
    second > 59
  )
    return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? new Date(time).toISOString() : null;
}

function readGroups(params) {
  return Object.fromEntries(
    Object.entries(GROUPS).map(([tab, supported]) => {
      const value = params.get(`${tab}_group`);
      return [tab, supported.includes(value) ? value : DEFAULT_GROUPS[tab]];
    }),
  );
}

/** Only these time/group controls belong in an Analytics link. */
export function parseAnalyticsQuery(query, now = new Date()) {
  const params = new URLSearchParams(query);
  const preset = ANALYTICS_RANGES.find(
    ({ value }) => value === params.get("preset"),
  );
  const start = instant(params.get("start"));
  const end = instant(params.get("end"));
  const span = Date.parse(end) - Date.parse(start);
  const groups = readGroups(params);
  // Stay below the API's 2,000-bucket ceiling for absolute day windows too.
  if (start && end && span > 0 && span <= MAX_ANALYTICS_WINDOW_MS) {
    return {
      preset: preset?.ms === span ? preset.value : "",
      start,
      end,
      granularity:
        span <= 6 * 3600000 ? "minute" : span <= 7 * DAY ? "hour" : "day",
      groups,
    };
  }
  const range = preset?.value || DEFAULT_RANGE;
  return { preset: range, ...rangeWindow(range, now), groups };
}

export function serializeAnalyticsQuery(state) {
  const params = new URLSearchParams();
  if (ANALYTICS_RANGES.some(({ value }) => value === state.preset))
    params.set("preset", state.preset);
  params.set("start", state.start);
  params.set("end", state.end);
  for (const [tab, supported] of Object.entries(GROUPS)) {
    const value = state.groups?.[tab];
    if (supported.includes(value) && value !== DEFAULT_GROUPS[tab])
      params.set(`${tab}_group`, value);
  }
  return params.toString();
}

/** Refresh advances only an explicitly rolling preset; a snapshot stays fixed. */
export function refreshAnalyticsQuery(state, now = new Date()) {
  return state.preset ? { ...state, ...rangeWindow(state.preset, now) } : state;
}

export function analyticsReadLabel(query, lastSuccessfulAt = 0) {
  const timestamp =
    !query.isPlaceholderData && query.dataUpdatedAt
      ? query.dataUpdatedAt
      : lastSuccessfulAt;
  return timestamp
    ? `Last successful read: ${new Date(timestamp).toISOString()}`
    : "No successful read yet";
}
