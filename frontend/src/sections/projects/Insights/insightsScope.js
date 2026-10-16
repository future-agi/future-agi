import { unitWord } from "src/sections/projects/observePopulation";
import { formatCount, formatTotal, formatWhen } from "./insightsFormat";

// What a report reads, and the request bodies that start it.
//
// A scope is {filters, label, preset?, window?}:
//   filters  the backend filters of a Traces / Sessions / Users list, including
//            its created_at window when it has one;
//   preset   "24h" | "7d" | "30d" — a window relative to now, re-anchored when
//            the report runs again; "fixed" — a handed-over set of rows (e.g.
//            every session Instant eval answered "No"), which cannot be
//            collected again from filters;
//   window   [startISO, endISO] — the backend reads each unit within it (for
//            users: only what they did in the window).

export const DATE_PRESETS = [
  { value: "24h", label: "Past 24 hours", hours: 24 },
  { value: "7d", label: "Past 7 days", hours: 24 * 7 },
  { value: "30d", label: "Past 30 days", hours: 24 * 30 },
];
export const FIXED_PRESET = "fixed";
export const SAMPLE_SIZES = [50, 100, 300, 500, 1000];
export const DEFAULT_SAMPLE_SIZE = 300;
const MIN_SAMPLE_SIZE = 10;
const LABEL_MAX = 300;

export const presetOption = (preset) =>
  DATE_PRESETS.find((option) => option.value === preset) || null;

export const dateItem = ([start, end]) => ({
  column_id: "created_at",
  filter_config: {
    filter_type: "datetime",
    filter_op: "between",
    filter_value: [new Date(start).toISOString(), new Date(end).toISOString()],
  },
});

export const isDateItem = (item) =>
  item?.column_id === "created_at" &&
  item?.filter_config?.filter_op === "between" &&
  Array.isArray(item?.filter_config?.filter_value);

export function presetWindow(preset, now = new Date()) {
  const option = presetOption(preset) || DATE_PRESETS[1];
  const start = new Date(now.getTime() - option.hours * 3600 * 1000);
  return [start.toISOString(), now.toISOString()];
}

/** Separate a list's created_at window from its other filters. */
export function splitFilters(filters = []) {
  const date = (filters || []).find(isDateItem);
  return {
    window: date ? date.filter_config.filter_value.slice(0, 2) : null,
    rest: (filters || []).filter((item) => !isDateItem(item)),
  };
}

export const formatWindow = (window) =>
  window?.length === 2
    ? `${formatWhen(window[0])} – ${formatWhen(window[1])}`
    : "";

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

/** "Past 7 days · 2 filters" */
export function scopeLabel({ preset, window, filters = [] }) {
  const parts = [];
  const option = presetOption(preset);
  if (option) parts.push(option.label);
  else if (window?.length === 2) parts.push(formatWindow(window));
  else parts.push("All time");
  const others = filters.filter((item) => !isDateItem(item)).length;
  if (others) parts.push(`${others} filter${others === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

/**
 * The scope a run is started with. `filters` are the non-date filters; the
 * window becomes their created_at item so list reads and the run agree.
 */
export function buildScope({ preset, window, filters = [], label }) {
  const rest = filters.filter((item) => !isDateItem(item));
  const scope = {
    filters: window?.length === 2 ? [...rest, dateItem(window)] : rest,
    label: (label || scopeLabel({ preset, window, filters: rest })).slice(
      0,
      LABEL_MAX,
    ),
  };
  if (presetOption(preset)) scope.preset = preset;
  if (window?.length === 2)
    scope.window = window.map((value) => new Date(value).toISOString());
  return scope;
}

export const fixedScope = (label) => ({
  label: String(label || "").slice(0, LABEL_MAX),
  preset: FIXED_PRESET,
});

export const isFixedScope = (scope) => scope?.preset === FIXED_PRESET;

/**
 * A saved scope for its next run: a relative preset moves its window to end
 * now; a custom window and a fixed set stay as they were.
 */
export function reanchorScope(scope = {}, now = new Date()) {
  if (!presetOption(scope.preset)) return scope;
  const { rest } = splitFilters(scope.filters || []);
  return buildScope({
    preset: scope.preset,
    window: presetWindow(scope.preset, now),
    filters: rest,
  });
}

/**
 * A run collects at most the newest MAX_POPULATION rows; when the scope held
 * more, its label says so, so "Sampled 300 of 2,000" is never mistaken for
 * the whole window.
 */
export function withCollectedNote(scope, collected) {
  if (!collected?.truncated) return scope;
  const note = `newest ${formatCount(collected.items.length)} of ${formatTotal(
    collected.total,
    collected.totalIsLowerBound,
  )}`;
  return {
    ...scope,
    label: `${scope.label || "All"} · ${note}`.slice(0, LABEL_MAX),
  };
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

/** POST insightsRuns for a new report. */
export function buildRunPayload({ projectId, draft, scope, population }) {
  const sampleSize = clampSample(draft.sampleSize);
  return {
    project_id: projectId,
    config: { ...buildConfig(draft), sample_size: sampleSize },
    scope,
    population: toPopulation(population),
    sample_size: sampleSize,
  };
}

/** POST insightsRuns for another run of a saved report. */
export function buildRerunPayload({
  projectId,
  configId,
  scope,
  population,
  sampleSize,
}) {
  const body = {
    project_id: projectId,
    config_id: configId,
    scope,
    population: toPopulation(population),
  };
  if (sampleSize) body.sample_size = clampSample(sampleSize);
  return body;
}

/**
 * "Analysing all 108 sessions" / "Analysing an even sample of 300 of 12,345
 * sessions" — and, when the list holds more than a run collects, that the
 * sample comes from the newest rows.
 */
export function sampleSentence({
  unit,
  total,
  isLowerBound,
  sampleSize,
  collectLimit,
}) {
  if (!Number.isFinite(total) || total <= 0) return "";
  const word = unitWord(unit, total);
  const totalText = `${formatCount(total)}${isLowerBound ? "+" : ""}`;
  if (total <= sampleSize && !isLowerBound)
    return `Analysing all ${totalText} ${word}`;
  const size = Math.min(sampleSize, total);
  const base = `Analysing an even sample of ${formatCount(size)} of ${totalText} ${word}`;
  if (collectLimit && (total > collectLimit || isLowerBound))
    return `${base}, drawn from the newest ${formatCount(collectLimit)}`;
  return base;
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
