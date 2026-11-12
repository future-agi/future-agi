import { format } from "date-fns";
import {
  buildApiFilterFromPanelRow,
  isNativeColumnType,
  normalizeColumnType,
} from "src/api/contracts/filter-contract";
import { ID_ONLY_FIELDS } from "./LLMTracing/idFields";

// How each Observe view configures its filter panel, so the views and the
// pages that ask about their rows (Instant eval, Insights) offer the same
// fields, values and catalog for a unit.

// The Sessions list's own fields (always available).
export const SESSION_FILTER_FIELDS = [
  { id: "session_id", name: "Session ID", category: "system", type: "string" },
  {
    id: "first_message",
    name: "First Message",
    category: "system",
    type: "string",
  },
  {
    id: "last_message",
    name: "Last Message",
    category: "system",
    type: "string",
  },
  { id: "user_id", name: "User ID", category: "system", type: "string" },
  { id: "duration", name: "Duration", category: "system", type: "number" },
  { id: "total_cost", name: "Total Cost", category: "system", type: "number" },
  {
    id: "total_tokens",
    name: "Total Tokens",
    category: "system",
    type: "number",
  },
  {
    id: "total_traces_count",
    name: "Total Traces",
    category: "system",
    type: "number",
  },
];

// The Users list's own fields.
export const USER_FILTER_FIELDS = [
  { id: "user_id", name: "User ID", category: "system", type: "string" },
  {
    id: "num_traces",
    name: "No. of Traces",
    category: "system",
    type: "number",
  },
  {
    id: "num_sessions",
    name: "No. of Sessions",
    category: "system",
    type: "number",
  },
  {
    id: "total_cost",
    name: "Total Cost ($)",
    category: "system",
    type: "number",
  },
  {
    id: "total_tokens",
    name: "Total Tokens",
    category: "system",
    type: "number",
  },
  {
    id: "avg_trace_latency",
    name: "Avg Latency / Trace (ms)",
    category: "system",
    type: "number",
  },
  {
    id: "num_llm_calls",
    name: "No. of LLM Calls",
    category: "system",
    type: "number",
  },
  {
    id: "eval_score",
    name: "Evals Pass Rate (%)",
    category: "system",
    type: "number",
  },
];

// The Traces list's and the Spans view's own start time, their grids'
// Timestamp column. The catalog lists it (traces and spans start_time), but the
// panel drops datetime catalog fields, so those views supply it, as Users
// supplies its native dates. The list API reads it as a bound on start_time:
// "on"/"not on" would match one microsecond and the column is never null, so
// only ranges are offered, "after" first.
export const TIMESTAMP_FILTER_FIELD = {
  id: "start_time",
  name: "Timestamp",
  category: "system",
  type: "datetime",
  searchAliases: ["date"],
  operators: [
    "greater_than",
    "less_than",
    "between",
    "greater_than_or_equal",
    "less_than_or_equal",
    "not_between",
  ],
};

/**
 * The filter panel's catalog settings for an ObserveToolbar mode.
 * Simulator projects render CallLogsGrid in the trace slot: the URL/tab stays
 * `trace`, but the visible rows and list endpoint use the voice-call field
 * contract. Session and user rows keep their native list/value transport,
 * while custom-attribute definitions and values live in the tracing catalog;
 * the session source for definitions is unsupported, and the spans source
 * misses trace catalog attributes.
 */
export function filterPanelSettings({ mode = "traces", tab, isSimulator }) {
  const effectiveFilterTab = isSimulator ? "voiceCalls" : tab;
  const propertyNamespace =
    effectiveFilterTab === "voiceCalls"
      ? "voice_calls"
      : mode === "sessions"
        ? "sessions"
        : mode === "users"
          ? "users"
          : "traces";
  const source =
    mode === "sessions" || mode === "users" ? "sessions" : "traces";
  const attributeSource =
    mode === "sessions" || mode === "users" ? "traces" : undefined;
  return { effectiveFilterTab, propertyNamespace, source, attributeSource };
}

const unitPanel = (mode, tab, extra) => {
  const settings = filterPanelSettings({ mode, tab });
  return {
    source: settings.source,
    propertyNamespace: settings.propertyNamespace,
    tab: settings.effectiveFilterTab,
    attributeSource: settings.attributeSource,
    isSpansView: false,
    filterFields: undefined,
    ...extra,
  };
};

// TraceFilterPanel props per unit for the Instant eval and Insights scope bars
// (ScopeFilterBar). They match what each view's toolbar sets, with one
// exception: on the Traces list and the Spans view, ObserveToolbar also adds
// the Timestamp field (TIMESTAMP_FILTER_FIELD). The scope bars leave it out,
// because a scope's date is its window. A Timestamp filter handed over from
// a view stays among the scope's filters and narrows that window.
export const OBSERVE_FILTER_CONFIG = {
  traces: unitPanel("traces", "trace"),
  spans: unitPanel("traces", "spans", { isSpansView: true }),
  sessions: unitPanel("sessions", undefined, {
    filterFields: SESSION_FILTER_FIELDS,
  }),
  users: unitPanel("users", undefined, { filterFields: USER_FILTER_FIELDS }),
};

export const filterConfigFor = (unit) =>
  OBSERVE_FILTER_CONFIG[unit] || OBSERVE_FILTER_CONFIG.traces;

const RANGE_OPS = new Set(["between", "not_between"]);

// A row's start time (the Timestamp field) bounds the Traces and Spans lists
// on start_time, which the API compares in UTC and reads a bare
// "yyyy-MM-ddTHH:mm" as UTC. The panel's date + time inputs hold the viewer's
// local wall time, so the filter carries that instant with the viewer's UTC
// offset, and a stored instant reopens as local wall time. Values already
// carrying a zone, or finer than milliseconds, pass through unchanged.
const ROW_TIME_COLUMN = "start_time";
const LOCAL_DATE_TIME =
  /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?)?$/;
const ZONED_DATE_TIME =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:\d{2})$/;

const isRowTimeFilter = (columnId, colType, isDateType) =>
  columnId === ROW_TIME_COLUMN && isDateType && isNativeColumnType(colType);

// Local wall time → the same instant with the viewer's offset. A bare day is
// that whole day: from 00:00, or through 23:59:59.999 as a range's upper
// bound, because the API reads a range as [start, end) and a 23:59 bound
// would leave out the day's last minute.
const toZonedTime = (value, { endOfDay = false } = {}) => {
  const match = typeof value === "string" && LOCAL_DATE_TIME.exec(value.trim());
  if (!match) return value;
  const [, year, month, day, hour, minute, second, fraction] = match;
  const dayEnd = hour === undefined && endOfDay;
  const date = new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    dayEnd ? 23 : Number(hour || 0),
    dayEnd ? 59 : Number(minute || 0),
    dayEnd ? 59 : Number(second || 0),
    dayEnd ? 999 : Number((fraction || "0").padEnd(3, "0")),
  );
  if (format(date, "yyyy-MM-dd") !== `${year}-${month}-${day}`) return value;
  return format(
    date,
    date.getMilliseconds()
      ? "yyyy-MM-dd'T'HH:mm:ss.SSSxxx"
      : "yyyy-MM-dd'T'HH:mm:ssxxx",
  );
};

// A stored instant → local wall time for the date + time input.
const toLocalTime = (value) => {
  if (typeof value !== "string" || !ZONED_DATE_TIME.test(value.trim())) {
    return value;
  }
  const date = new Date(value.trim());
  if (Number.isNaN(date.getTime())) return value;
  if (date.getMilliseconds()) return format(date, "yyyy-MM-dd'T'HH:mm:ss.SSS");
  if (date.getSeconds()) return format(date, "yyyy-MM-dd'T'HH:mm:ss");
  return format(date, "yyyy-MM-dd'T'HH:mm");
};

const COL_TYPE_REVERSE_MAP = {
  SPAN_ATTRIBUTE: "attribute",
  SYSTEM_METRIC: "system",
  EVAL_METRIC: "eval",
  ANNOTATION: "annotation",
};

/** One backend filter as a filter-panel row. */
export function apiFilterToPanelRow(gf) {
  const rawOp = gf.filter_config?.filter_op || "equals";
  const rawType = gf.filter_config?.filter_type;
  const rawVal = gf.filter_config?.filter_value;
  // Trust explicit `filter_type` only; ops are shared across types.
  const isNumberType = rawType === "number";
  const isDateType =
    rawType === "datetime" || rawType === "date" || rawType === "timestamp";
  const isBooleanType = rawType === "boolean";
  const isArrayType = rawType === "array" || rawType === "list";
  const isMapType =
    rawType === "map" ||
    rawType === "object" ||
    (rawType === "json" &&
      rawVal !== null &&
      typeof rawVal === "object" &&
      !Array.isArray(rawVal));
  const isRange = RANGE_OPS.has(rawOp);
  let value;
  if (isRange) {
    // Normalize to a 2-element string array for the TextField pair.
    if (Array.isArray(rawVal)) {
      value = rawVal.map((v) => (v == null ? "" : String(v)));
    } else if (rawVal != null) {
      value = String(rawVal)
        .split(",")
        .map((v) => v.trim());
    } else {
      value = ["", ""];
    }
  } else if (isBooleanType) {
    // MUI Select needs "true"/"false" strings; backend uses native bool.
    value = rawVal === true || rawVal === "true" ? "true" : "false";
  } else if (isNumberType || isDateType) {
    value = rawVal != null ? String(rawVal) : "";
  } else if (isMapType) {
    value = rawVal && typeof rawVal === "object" ? rawVal : "";
  } else {
    // Canonical list members and exact scalar strings must stay intact.
    // Splitting commas also corrupts aligned attribute_value_types.
    value = Array.isArray(rawVal)
      ? rawVal
      : rawVal !== undefined && rawVal !== null && rawVal !== ""
        ? [rawVal]
        : [];
  }
  // Derive fieldCategory from col_type (reverse of colTypeMap)
  const explicitColType = normalizeColumnType(
    gf.filter_config?.col_type || gf.col_type,
  );
  if (isRowTimeFilter(gf.column_id, explicitColType, isDateType)) {
    value = Array.isArray(value) ? value.map(toLocalTime) : toLocalTime(value);
  }
  const isDirectIdFilter = ID_ONLY_FIELDS.has(gf.column_id) && !explicitColType;
  const rawColType =
    explicitColType || (isDirectIdFilter ? undefined : "SYSTEM_METRIC");
  const rawFilterType = gf.filter_config?.filter_type;
  const isGlobalAnnotatorFilter =
    gf.column_id === "annotator" &&
    (isNativeColumnType(explicitColType) || explicitColType === "ANNOTATION");
  // Auto-migrate legacy saved views: thumbs annotations used to be
  // stored as filter_type=categorical with values like ["Thumbs Up",
  // "Thumbs Down"]. Detect and upgrade to the dedicated `thumbs` type
  // so the BE thumbs branch handles them and the panel renders the
  // right operators/picker.
  const looksLikeThumbsValues = (() => {
    if (rawColType !== "ANNOTATION") return false;
    if (rawFilterType !== "categorical") return false;
    const vals = Array.isArray(value) ? value : value ? [value] : [];
    if (vals.length === 0) return false;
    const tokens = new Set(["thumbs up", "thumbs down", "up", "down"]);
    return vals.every((v) => tokens.has(String(v).trim().toLowerCase()));
  })();
  return {
    field: gf.column_id,
    registryId: gf.property_id,
    fieldName:
      gf.display_name || (isGlobalAnnotatorFilter ? "Annotator" : null),
    fieldCategory: isDirectIdFilter
      ? undefined
      : isGlobalAnnotatorFilter
        ? "annotation"
        : COL_TYPE_REVERSE_MAP[rawColType] || "system",
    fieldType: isGlobalAnnotatorFilter
      ? "annotator"
      : isBooleanType
        ? "boolean"
        : isNumberType
          ? "number"
          : isMapType
            ? "map"
            : isArrayType || rawFilterType === "json"
              ? "array"
              : isDateType
                ? rawType
                : rawFilterType === "thumbs" || looksLikeThumbsValues
                  ? "thumbs"
                  : rawFilterType === "categorical"
                    ? "categorical"
                    : rawFilterType === "text" && rawColType === "ANNOTATION"
                      ? "text"
                      : "string",
    apiColType:
      explicitColType ||
      (isDirectIdFilter
        ? undefined
        : isGlobalAnnotatorFilter
          ? "SYSTEM_METRIC"
          : rawColType),
    operator: rawOp,
    value,
    valueTypes: gf.filter_config?.attribute_value_types,
  };
}

/** Backend filters as filter-panel rows; null when there are none. */
export const apiFiltersToPanelRows = (filters) =>
  filters?.length ? filters.map(apiFilterToPanelRow) : null;

/** One panel row as a backend filter; a Timestamp carries the viewer's zone. */
const panelRowToApiFilter = (row) => {
  const filter = buildApiFilterFromPanelRow(row);
  const config = filter.filter_config;
  if (
    !isRowTimeFilter(
      filter.column_id,
      config.col_type,
      config.filter_type === "datetime",
    )
  ) {
    return filter;
  }
  const value = Array.isArray(config.filter_value)
    ? config.filter_value.map((item, index) =>
        toZonedTime(item, {
          endOfDay: RANGE_OPS.has(config.filter_op) && index === 1,
        }),
      )
    : toZonedTime(config.filter_value);
  return { ...filter, filter_config: { ...config, filter_value: value } };
};

/** The panel's rows as backend filters. */
export const panelRowsToApiFilters = (rows) =>
  (rows || []).map(panelRowToApiFilter);

/** The human name of a unit's own field, when the filter has none. */
export function fieldName(unit, columnId) {
  const fields = filterConfigFor(unit).filterFields || [];
  return fields.find((field) => field.id === columnId)?.name;
}

// Traces and spans read one vocabulary, but each names its rows its own way:
// a span-name or span-id filter means nothing to traces, and the Spans panel
// names a span's name `span_name`, never `name`.
const OWN_COLUMNS = {
  traces: new Set(["span_id", "span_name"]),
  spans: new Set(["name"]),
};
const FAMILY = {
  traces: "traces",
  spans: "traces",
  sessions: "sessions",
  users: "users",
};

/**
 * The filters that still apply when a question moves from one unit to
 * another: within traces and spans all but the other's own columns; between
 * traces, sessions and users only the date carries over, since the same
 * column (a cost, a user id) counts something else on each.
 */
export function filtersForUnit(filters = [], fromUnit, toUnit) {
  if (fromUnit === toUnit) return { kept: filters, dropped: [] };
  const kept = [];
  const dropped = [];
  const sameFamily = FAMILY[fromUnit] && FAMILY[fromUnit] === FAMILY[toUnit];
  (filters || []).forEach((item) => {
    const ok = sameFamily && !OWN_COLUMNS[toUnit]?.has(item?.column_id);
    (ok ? kept : dropped).push(item);
  });
  return { kept, dropped };
}
