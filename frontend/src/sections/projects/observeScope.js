import { formatDayRange } from "./Insights/insightsFormat";
import { DATE_OPTION_LABELS } from "./LLMTracing/DateRangePill";
import { filtersForUnit } from "./observeFilterConfig";
import { UNITS } from "./observePopulation";
import { observePresetDateFilter } from "./timeWindowPresets";

// The rows an Instant eval question or an Insights report reads: one Observe
// view's rows, as its toolbar narrows them. A scope is
//   {unit, filters, dateOption, window, label, ids?}
//   unit        "traces" | "spans" | "sessions" | "users"
//   filters     the view's backend filters, without its date window
//   dateOption  an Observe preset key ("Today", "7D", …) or "Custom"
//   window      [isoStart, isoEnd]: the preset resolved, or the custom range
//   label       "Past 7D · 2 filters", "Custom range · 1 filter"
//   search?     the Users view's search text, which narrows users
//   ids?        only these rows (a selection handed over); filters then
//               don't apply
// A preset's window comes from the same helper every Observe date site uses,
// so the rows asked about are exactly the rows the view lists.

export const CUSTOM = "Custom";
export const DEFAULT_DATE_OPTION = "7D";

// The grid's filters carry the date window as a created_at "between" item.
export const isWindowItem = (item) =>
  item?.column_id === "created_at" &&
  item?.filter_config?.filter_op === "between" &&
  Array.isArray(item?.filter_config?.filter_value);

const toIso = (value) => {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
};

/** [start, end] in ISO, or null when either end isn't a date. */
export function isoWindow(window) {
  if (!Array.isArray(window) || window.length < 2) return null;
  const pair = window.slice(0, 2).map(toIso);
  return pair.every(Boolean) ? pair : null;
}

export const windowItem = (window) => ({
  column_id: "created_at",
  filter_config: {
    filter_type: "datetime",
    filter_op: "between",
    filter_value: isoWindow(window),
  },
});

/** A view's created_at window apart from its other filters. */
export function splitWindow(apiFilters = []) {
  let window = null;
  const filters = [];
  (apiFilters || []).forEach((item) => {
    if (isWindowItem(item))
      window = item.filter_config.filter_value.slice(0, 2);
    else if (item) filters.push(item);
  });
  return { window, filters };
}

/** A preset's window now, as the views send it; null for Custom. */
export const presetWindow = (dateOption, now = new Date()) =>
  isoWindow(observePresetDateFilter(dateOption, now));

export const isPresetOption = (dateOption) =>
  Boolean(dateOption && observePresetDateFilter(dateOption));

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** "Past 7D", "Today", "Custom range"; "All time" without a window. */
export function dateLabel(dateOption, window) {
  if (dateOption && dateOption !== CUSTOM)
    return DATE_OPTION_LABELS[dateOption] || `Past ${dateOption}`;
  return window || dateOption === CUSTOM
    ? DATE_OPTION_LABELS[CUSTOM]
    : "All time";
}

const SEARCH_LABEL_MAX = 24;

/**
 * "Past 7D · 2 filters · “acme”": the date, every filter but the window, and
 * a Users search in quotes.
 */
export function scopeLabel({ dateOption, window, filters = [], search } = {}) {
  const count = (filters || []).filter(
    (item) => item && !isWindowItem(item),
  ).length;
  const parts = [dateLabel(dateOption, window)];
  if (count) parts.push(plural(count, "filter"));
  const text = search?.trim();
  if (text)
    parts.push(
      `“${text.length > SEARCH_LABEL_MAX ? `${text.slice(0, SEARCH_LABEL_MAX - 1)}…` : text}”`,
    );
  return parts.join(" · ");
}

/**
 * The date option Instant eval keeps with a run: an Observe preset, or
 * "Custom" for any other window (a legacy report's "24h" included).
 */
export const datePresetOf = (dateOption) =>
  isPresetOption(dateOption) || dateOption === CUSTOM
    ? dateOption
    : dateOption
      ? CUSTOM
      : undefined;

/** A scope with its window in ISO and its label to match. */
export function makeScope({
  unit,
  filters = [],
  dateOption,
  window,
  search,
  ids,
}) {
  // A window among the filters is the scope's, unless one is given.
  const split = splitWindow(filters);
  const ownWindow = window || split.window;
  const scope = {
    unit,
    filters: split.filters,
    dateOption: dateOption || (ownWindow ? CUSTOM : DEFAULT_DATE_OPTION),
    window: isoWindow(ownWindow),
  };
  if (!scope.window && isPresetOption(scope.dateOption))
    scope.window = presetWindow(scope.dateOption);
  if (search?.trim()) scope.search = search.trim();
  scope.label = scopeLabel(scope);
  if (Array.isArray(ids) && ids.length) scope.ids = ids;
  return scope;
}

/** A new question or report with nothing handed over: the past week. */
export const defaultScope = (unit, now = new Date()) =>
  makeScope({
    unit,
    dateOption: DEFAULT_DATE_OPTION,
    window: presetWindow(DEFAULT_DATE_OPTION, now),
  });

/**
 * The scope a hand-over opens with: the view's filters and its date as the
 * view had it. A preset is resolved against now, so "Today" means today; a
 * window without a preset is a custom range; a seed without a window starts
 * from the past week.
 */
export function scopeFromSeed(seed, unit = seed?.unit, now = new Date()) {
  const { window, filters } = splitWindow(seed?.filters);
  const seedWindow = window || seed?.window || null;
  const option = seed?.dateOption;
  const search = unit === "users" ? seed?.search : undefined;
  if (option && option !== CUSTOM) {
    const fresh = presetWindow(option, now);
    if (fresh)
      return makeScope({
        unit,
        filters,
        search,
        dateOption: option,
        window: fresh,
      });
  }
  if (isoWindow(seedWindow))
    return makeScope({
      unit,
      filters,
      search,
      dateOption: CUSTOM,
      window: seedWindow,
    });
  return makeScope({
    unit,
    filters,
    search,
    dateOption: DEFAULT_DATE_OPTION,
    window: presetWindow(DEFAULT_DATE_OPTION, now),
  });
}

/** The backend filters a scope reads: its filters plus its window. */
export const scopeApiFilters = (scope) =>
  scope?.window
    ? [...(scope.filters || []), windowItem(scope.window)]
    : [...(scope?.filters || [])];

/** The same scope with other filters, relabelled. */
export const withFilters = (scope, filters) =>
  makeScope({ ...scope, filters: filters || [] });

const atMidnight = (date) =>
  date.getHours() === 0 &&
  date.getMinutes() === 0 &&
  date.getSeconds() === 0 &&
  date.getMilliseconds() === 0;

/**
 * A range picked in the custom picker, which picks days: one ending on a day
 * reads that whole day, so 22–23 Oct is both days.
 */
export function throughLastDay([from, to]) {
  const end = new Date(to);
  if (Number.isNaN(end.getTime()) || !atMidnight(end)) return [from, to];
  end.setHours(23, 59, 59, 999);
  return [from, end];
}

/**
 * "Oct 22–23": the days a custom window reads. A window ending at midnight
 * (one an Observe view handed over) reads up to the day before.
 */
export function customRangeLabel(window) {
  if (!Array.isArray(window) || window.length < 2) return undefined;
  const from = new Date(window[0]);
  const to = new Date(window[1]);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()))
    return undefined;
  const last = atMidnight(to) && to > from ? new Date(to.getTime() - 1) : to;
  return formatDayRange(from, last);
}

/** The same scope over another date: a preset key and its window. */
export const withDate = (scope, dateOption, window) =>
  makeScope({ ...scope, dateOption, window });

/**
 * The same date for another unit: the filters it can't read are dropped (a
 * search only narrows users), with a sentence saying so, or null.
 */
export function scopeForUnit(scope, unit) {
  if (unit === scope.unit) return { scope, notice: null };
  const { kept, dropped } = filtersForUnit(scope.filters, scope.unit, unit);
  const keepSearch = unit === "users";
  const next = makeScope({
    ...scope,
    unit,
    filters: kept,
    search: keepSearch ? scope.search : undefined,
    ids: undefined,
  });
  // "2 filters and the search don't apply to sessions, so they were removed."
  const gone = [
    dropped.length && plural(dropped.length, "filter"),
    scope.search && !keepSearch && "the search",
  ].filter(Boolean);
  const one = gone.length === 1 && dropped.length <= 1;
  const what = gone.join(" and ");
  const notice = gone.length
    ? `${what[0].toUpperCase()}${what.slice(1)} ${one ? "doesn't" : "don't"} apply to ${
        (UNITS[unit] || UNITS.traces).plural
      }, so ${one ? "it was" : "they were"} removed. The date range stays.`
    : null;
  return { scope: next, notice };
}

/**
 * A scope for its next run: a preset's window moves to now, a custom range
 * stays. Returns the scope itself when nothing moves.
 */
export function reanchorScope(scope, now = new Date()) {
  if (!scope || !isPresetOption(scope.dateOption)) return scope;
  const fresh = presetWindow(scope.dateOption, now);
  if (scope.window?.[0] === fresh[0] && scope.window?.[1] === fresh[1])
    return scope;
  return { ...scope, window: fresh };
}

/**
 * What a view hands over: its backend filters (window included), its date
 * preset and that window, and a label in the toolbar's words.
 */
export function viewSeed({ unit, filters = [], dateFilter, search }) {
  const { window } = splitWindow(filters);
  const dateOption = dateFilter?.dateOption || (window ? CUSTOM : undefined);
  const text = search?.trim() || undefined;
  return {
    unit,
    filters,
    dateOption,
    ...(window ? { window } : {}),
    ...(text ? { search: text } : {}),
    label: scopeLabel({ dateOption, window, filters, search: text }),
  };
}
