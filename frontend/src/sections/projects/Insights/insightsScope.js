import { unitWord } from "src/sections/projects/observePopulation";
import { DATE_OPTION_LABELS } from "src/sections/projects/LLMTracing/DateRangePill";
import {
  CUSTOM,
  customRangeLabel,
  isPresetOption,
  isoWindow,
  reanchorScope as reanchorPreset,
  scopeApiFilters,
  scopeLabel,
  splitWindow,
} from "src/sections/projects/observeScope";
import { formatCount } from "./insightsFormat";

// What a report reads, and the request bodies that start it.
//
// The page edits the shared Observe scope (see observeScope): {unit, filters,
// dateOption, window, label}. A report stores it as {filters, label, preset,
// window}:
//   filters  the backend filters of a Traces / Sessions / Users list, its
//            created_at window included; the server reads the rows they match
//   preset   an Observe date preset ("7D", "Today", …), re-anchored to now
//            when the report runs again, or "Custom" for a fixed range;
//            "fixed" is a handed-over set of rows (e.g. every session Instant
//            eval answered "No"), which cannot be read again from filters
//   window   [startISO, endISO] — the backend reads each unit within it (for
//            users: only what they did in the window).
// Reports saved before Observe's presets keep working: "7d" and "30d" read as
// "7D" and "30D", and "24h", which no Observe preset matches, stays a rolling
// 24 hours.

export const FIXED_PRESET = "fixed";
export const LEGACY_DAY = "24h";
const LEGACY_PRESETS = { "7d": "7D", "30d": "30D" };
const DAY_MS = 24 * 3600 * 1000;
export const SAMPLE_SIZES = [50, 100, 300, 500, 1000];

/** The sizes to offer: the fixed ones, and the one chosen if it isn't. */
export const sampleSizeOptions = (current) =>
  Number.isInteger(current) && !SAMPLE_SIZES.includes(current)
    ? [...SAMPLE_SIZES, current].sort((a, b) => a - b)
    : SAMPLE_SIZES;
export const DEFAULT_SAMPLE_SIZE = 300;
const MIN_SAMPLE_SIZE = 10;
const LABEL_MAX = 300;

export const isFixedScope = (scope) => scope?.preset === FIXED_PRESET;

/** A saved report's latest scope: the report's own, else its last run's. */
export const lastScope = (config) =>
  config?.scope && Object.keys(config.scope).length
    ? config.scope
    : config?.latest_run?.scope || {};

/** The Observe date option a saved scope reads; null for all time. */
export function savedDateOption(saved = {}) {
  const { preset } = saved;
  if (preset === LEGACY_DAY) return LEGACY_DAY;
  if (LEGACY_PRESETS[preset]) return LEGACY_PRESETS[preset];
  if (preset === CUSTOM || isPresetOption(preset)) return preset;
  return splitWindow(saved.filters).window || saved.window ? CUSTOM : null;
}

const legacyDayWindow = (now) => [
  new Date(now.getTime() - DAY_MS).toISOString(),
  now.toISOString(),
];

/**
 * A scope for its next run: a preset's window moves to now (a legacy "24h"
 * to the 24 hours ending now); a custom range, all time and a fixed set stay.
 */
export function reanchorScope(scope, now = new Date()) {
  if (scope?.dateOption === LEGACY_DAY)
    return { ...scope, window: legacyDayWindow(now) };
  return reanchorPreset(scope, now);
}

/** A saved scope as the scope bar edits it, its window moved to now. */
export function editableScope(saved = {}, unit, now = new Date()) {
  const { window, filters } = splitWindow(saved.filters);
  const dateOption = savedDateOption(saved);
  const scope = reanchorScope(
    {
      unit,
      filters,
      dateOption: dateOption || undefined,
      window: isoWindow(window || saved.window),
      ...(unit === "users" && saved.search?.trim()
        ? { search: saved.search.trim() }
        : {}),
    },
    now,
  );
  return { ...scope, label: scopeLabel(scope) };
}

/** What the date pill says for a date the Observe presets don't name. */
export const scopeDateLabel = (scope) =>
  scope?.dateOption === LEGACY_DAY
    ? "Past 24h"
    : !scope?.window
      ? "All time"
      : undefined;

/**
 * A stored scope's label as a report shows it: a custom range names the days
 * it read ("Oct 22–23 · 1 filter"), as the scope bar does; any other scope
 * reads as stored.
 */
export function shownScopeLabel(scope) {
  const label = scope?.label || "";
  const custom = DATE_OPTION_LABELS[CUSTOM];
  const days =
    scope?.preset === CUSTOM && label.startsWith(custom)
      ? customRangeLabel(scope.window)
      : undefined;
  return days ? days + label.slice(custom.length) : label;
}

/** The scope a report stores and a run is started with. */
export function storedScope(scope) {
  const stored = {
    filters: scopeApiFilters(scope),
    label: (scope.label || scopeLabel(scope)).slice(0, LABEL_MAX),
  };
  if (scope.dateOption) stored.preset = scope.dateOption;
  if (scope.window) stored.window = scope.window;
  if (scope.search) stored.search = scope.search;
  return stored;
}

/** What the server counts for a scope: its filters (window in) and search. */
export const countedScope = (scope) => ({
  filters: scopeApiFilters(scope),
  ...(scope?.search ? { search: scope.search } : {}),
});

/**
 * A report's scope as a hand-over to the Ask page, so a question measures the
 * rows the report read: its filters, search and date, a preset's window moved
 * to now. A legacy "24h" goes as a custom range (Instant eval keeps Observe
 * presets only). Null for rows handed over, and for all time, which the Ask
 * page has no window for.
 */
export function askSeed(saved = {}, unit, now = new Date()) {
  if (isFixedScope(saved)) return null;
  const scope = editableScope(saved, unit, now);
  if (!scope.window) return null;
  const dateOption =
    scope.dateOption === LEGACY_DAY ? CUSTOM : scope.dateOption || CUSTOM;
  return {
    unit,
    filters: scopeApiFilters(scope),
    dateOption,
    window: scope.window,
    ...(scope.search ? { search: scope.search } : {}),
    label: scopeLabel({ ...scope, dateOption }),
  };
}

export const fixedScope = (label) => ({
  label: String(label || "").slice(0, LABEL_MAX),
  preset: FIXED_PRESET,
});

const OP_LABEL = {
  equals: "is",
  not_equals: "is not",
  in: "is one of",
  not_in: "is not one of",
  contains: "contains",
  not_contains: "does not contain",
  greater_than: ">",
  greater_than_or_equal: "≥",
  less_than: "<",
  less_than_or_equal: "≤",
  between: "between",
  not_between: "not between",
  is_null: "is empty",
  is_not_null: "is set",
};

const humanColumn = (id) =>
  String(id || "filter")
    .replace(/[_.]+/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());

/** "Status is ERROR", "Did the agent … · No" for an answer filter. */
export function describeFilter(item) {
  const config = item?.filter_config || {};
  if (config.col_type === "EVAL_METRIC")
    return `${[].concat(config.filter_value ?? []).join(" or ")} · ${item.display_name || "answer"}`;
  const value = Array.isArray(config.filter_value)
    ? config.filter_value.join(", ")
    : `${config.filter_value ?? ""}`;
  const field = item?.display_name || humanColumn(item?.column_id);
  return `${field} ${OP_LABEL[config.filter_op] || config.filter_op || ""} ${value}`
    .replace(/\s+/g, " ")
    .trim();
}

/** Rows from a list or a hand-off, cleaned to what the run endpoint accepts. */
export function toPopulation(items = []) {
  const seen = new Set();
  const out = [];
  items.forEach((item) => {
    const id = item?.id == null ? "" : String(item.id).trim();
    if (!id || id.length > 255 || seen.has(id)) return;
    seen.add(id);
    const row = { id };
    if (item.time) row.time = String(item.time).slice(0, 64);
    if (item.user_id) row.user_id = String(item.user_id).slice(0, 255);
    out.push(row);
  });
  return out;
}

const clampSample = (size) =>
  Math.min(
    1000,
    Math.max(MIN_SAMPLE_SIZE, Math.round(size || DEFAULT_SAMPLE_SIZE)),
  );

export const cleanAttributes = (attributes = []) =>
  attributes
    .filter((a) => a?.key?.trim())
    .map((a) => ({
      key: a.key.trim(),
      type: a.type || "boolean",
      description: (a.description || "").trim().slice(0, 300),
      filter_by: a.type === "boolean" ? Boolean(a.filter_by) : false,
    }));

/** The config part shared by draft-preview and new-run requests. */
export function buildConfig(draft) {
  const config = {
    name: (draft.name || "").trim().slice(0, 255),
    unit: draft.unit,
    lens: draft.lens,
    question: (draft.question || "").trim().slice(0, 500),
    agent_description: (draft.agentDescription || "").trim().slice(0, 500),
    instructions: (draft.instructions || "").trim().slice(0, 1000),
    attributes: cleanAttributes(draft.attributes),
  };
  if (draft.sampleSize) config.sample_size = clampSample(draft.sampleSize);
  return config;
}

// Rows handed over ride along; a scope of filters is read on the server.
const withPopulation = (body, population) =>
  population?.length ? { ...body, population: toPopulation(population) } : body;

/** POST insightsRuns for a new report. */
export function buildRunPayload({ projectId, draft, scope, population }) {
  const sampleSize = clampSample(draft.sampleSize);
  return withPopulation(
    {
      project_id: projectId,
      config: { ...buildConfig(draft), sample_size: sampleSize },
      scope,
      sample_size: sampleSize,
    },
    population,
  );
}

/** POST insightsRuns for another run of a saved report. */
export function buildRerunPayload({
  projectId,
  configId,
  scope,
  population,
  sampleSize,
  resetTaxonomy = false,
}) {
  const body = {
    project_id: projectId,
    config_id: configId,
    scope,
  };
  if (sampleSize) body.sample_size = clampSample(sampleSize);
  // "Rediscover categories" goes with the run: the server forgets the saved
  // categories only once the run is allowed to start.
  if (resetTaxonomy) body.reset_taxonomy = true;
  return withPopulation(body, population);
}

/**
 * "Analysing all 108 sessions" / "Analysing a random sample of 300 of 12,345
 * sessions". The server draws a scope's sample uniformly at random; rows
 * handed over (`handedOver`) are sampled evenly across them.
 */
export function sampleSentence({
  unit,
  total,
  isLowerBound,
  sampleSize,
  handedOver = false,
}) {
  if (!Number.isFinite(total) || total <= 0) return "";
  const word = unitWord(unit, total);
  const totalText = `${formatCount(total)}${isLowerBound ? "+" : ""}`;
  if (total <= sampleSize && !isLowerBound)
    return `Analysing all ${totalText} ${word}`;
  const size = Math.min(sampleSize, total);
  const kind = handedOver ? "an even sample" : "a random sample";
  return `Analysing ${kind} of ${formatCount(size)} of ${totalText} ${word}`;
}

export const effectiveSample = (total, sampleSize) =>
  Number.isFinite(total) ? Math.min(total, sampleSize) : sampleSize;

/**
 * Evenly spaced picks, so a preview sees the window's spread rather than one
 * busy hour; each `offset` shifts to a different, still spread, set.
 */
export function spreadPick(items = [], count = 3, offset = 0) {
  if (items.length <= count) return items.slice();
  const step = items.length / count;
  const shift = offset * Math.max(1, Math.floor(step / 2));
  return Array.from(
    { length: count },
    (_, i) => items[(Math.floor(i * step) + shift) % items.length],
  );
}
