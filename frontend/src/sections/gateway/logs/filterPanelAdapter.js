/**
 * Request log filters <-> shared Filter panel.
 *
 * The page keeps its filters as flat URL-synced strings (`minLatency`,
 * `isError: "true"`, `model: "gpt-4o,claude-3"` ...). Ranges are a min and a
 * max key; the panel shows each as one row ("between", "at least", "at most").
 */
const RANGES = [
  {
    id: "status_code",
    label: "Status code",
    min: "statusCodeMin",
    max: "statusCodeMax",
  },
  {
    id: "latency",
    label: "Latency (ms)",
    min: "minLatency",
    max: "maxLatency",
  },
  { id: "cost", label: "Cost ($)", min: "minCost", max: "maxCost" },
];
const DATE_RANGE = {
  id: "started",
  label: "Started",
  min: "startedAfter",
  max: "startedBefore",
};
const LISTS = [
  { id: "model", label: "Model", key: "model" },
  { id: "provider", label: "Provider", key: "provider" },
];
const TEXTS = [
  { id: "user_id", label: "User ID", key: "userId" },
  { id: "session_id", label: "Session ID", key: "sessionId" },
];
const FLAGS = [
  { id: "is_error", label: "Errors only", key: "isError" },
  { id: "cache_hit", label: "Cache hits only", key: "cacheHit" },
  {
    id: "guardrail_triggered",
    label: "Guardrail triggered",
    key: "guardrailTriggered",
  },
];

/** URL keys this panel owns; everything else in the URL is left alone. */
export const REQUEST_FILTER_KEYS = [
  DATE_RANGE.min,
  DATE_RANGE.max,
  ...RANGES.flatMap(({ min, max }) => [min, max]),
  ...LISTS.map(({ key }) => key),
  ...TEXTS.map(({ key }) => key),
  ...FLAGS.map(({ key }) => key),
];

const RANGE_OPERATORS = [
  "between",
  "greater_than_or_equal",
  "less_than_or_equal",
];
const DATE_OPERATORS = ["between", "greater_than", "less_than"];

export const requestFilterFields = (availableModels = []) => [
  {
    id: DATE_RANGE.id,
    label: DATE_RANGE.label,
    type: "date",
    operators: DATE_OPERATORS,
  },
  {
    id: "model",
    label: "Model",
    type: "enum",
    multiple: true,
    options: availableModels,
    operators: ["in"],
  },
  { id: "provider", label: "Provider", type: "text", operators: ["in"] },
  ...RANGES.map(({ id, label }) => ({
    id,
    label,
    type: "number",
    operators: RANGE_OPERATORS,
  })),
  // The user and session filters take a single id.
  ...TEXTS.map(({ id, label }) => ({
    id,
    label,
    type: "text",
    operators: ["in"],
    multiple: false,
  })),
  ...FLAGS.map(({ id, label }) => ({
    id,
    label,
    type: "enum",
    options: [{ value: "true", label: "Yes" }],
    operators: ["equals"],
  })),
];

const split = (value) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

export const toPanelValue = (filters = {}) => {
  const rows = [];

  const range = ({ id, min, max }, type, ops) => {
    const lo = filters[min];
    const hi = filters[max];
    if (lo && hi) {
      rows.push({
        field: id,
        fieldType: type,
        operator: "between",
        value: [String(lo), String(hi)],
      });
    } else if (lo) {
      rows.push({
        field: id,
        fieldType: type,
        operator: ops.min,
        value: String(lo),
      });
    } else if (hi) {
      rows.push({
        field: id,
        fieldType: type,
        operator: ops.max,
        value: String(hi),
      });
    }
  };

  range(DATE_RANGE, "date", { min: "greater_than", max: "less_than" });
  LISTS.forEach(({ id, key }) => {
    const values = split(filters[key]);
    if (values.length) {
      rows.push({
        field: id,
        fieldType: "string",
        operator: "in",
        value: values,
      });
    }
  });
  RANGES.forEach((definition) =>
    range(definition, "number", {
      min: "greater_than_or_equal",
      max: "less_than_or_equal",
    }),
  );
  TEXTS.forEach(({ id, key }) => {
    if (filters[key]) {
      rows.push({
        field: id,
        fieldType: "string",
        operator: "in",
        value: [String(filters[key])],
      });
    }
  });
  FLAGS.forEach(({ id, key }) => {
    if (filters[key] === "true") {
      rows.push({
        field: id,
        fieldType: "categorical",
        operator: "equals",
        value: ["true"],
      });
    }
  });
  return rows;
};

const first = (value) => (Array.isArray(value) ? value[0] : value);
const filled = (value) => value !== undefined && value !== null && value !== "";

/** Panel rows -> the flat keys this panel owns (empty ones left out). */
export const fromPanelValue = (rows = []) => {
  const out = {};
  const byField = Object.fromEntries(
    (rows || []).map((row) => [row.field, row]),
  );

  const range = ({ id, min, max }) => {
    const row = byField[id];
    if (!row) return;
    const values = Array.isArray(row.value) ? row.value : [row.value];
    if (row.operator === "between") {
      if (filled(values[0])) out[min] = String(values[0]);
      if (filled(values[1])) out[max] = String(values[1]);
    } else if (
      row.operator === "greater_than" ||
      row.operator === "greater_than_or_equal"
    ) {
      if (filled(values[0])) out[min] = String(values[0]);
    } else if (
      row.operator === "less_than" ||
      row.operator === "less_than_or_equal"
    ) {
      if (filled(values[0])) out[max] = String(values[0]);
    }
  };

  range(DATE_RANGE);
  RANGES.forEach(range);
  LISTS.forEach(({ id, key }) => {
    const row = byField[id];
    const values = (row ? [].concat(row.value) : []).filter(filled);
    if (values.length) out[key] = values.join(",");
  });
  TEXTS.forEach(({ id, key }) => {
    const row = byField[id];
    const value = row ? first(row.value) : undefined;
    if (filled(value)) out[key] = String(value);
  });
  FLAGS.forEach(({ id, key }) => {
    if (byField[id] && filled(first(byField[id].value))) out[key] = "true";
  });
  return out;
};
