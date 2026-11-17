import { parsePath, resolvePath } from "src/sections/common/Values/faPath";

export const SESSION_TURN_METRICS = {
  total_latency_ms: "Latency",
  total_tokens: "Total tokens",
  input_tokens: "Input tokens",
  output_tokens: "Output tokens",
  total_cost: "Cost",
  user_id: "User ID",
  start_time: "Start time",
};
export const SESSION_TURN_COLUMNS = ["input", "output"];
export const DEFAULT_SESSION_TURN_FIELDS = [
  { kind: "value", label: "Human", path: "input" },
  { kind: "value", label: "AI", path: "output" },
  ...Object.keys(SESSION_TURN_METRICS).map((metric) => ({
    kind: "metric",
    metric,
  })),
];

export function sessionTurnFieldError(field) {
  if (!field || typeof field !== "object") return "Choose a field.";
  if (field.kind === "metric") {
    return Object.hasOwn(SESSION_TURN_METRICS, field.metric)
      ? null
      : "Choose a supported metric.";
  }
  if (field.kind !== "value") return "Choose a value or metric.";
  if (typeof field.label !== "string" || !field.label.trim())
    return "Enter a label.";
  const parsed = parsePath(field.path, { columns: SESSION_TURN_COLUMNS });
  if (!parsed.ok || !parsed.column)
    return "Enter a valid path starting with input or output.";
  return null;
}

export function normalizeSessionTurnFields(fields) {
  // An empty list is intentional. Corrupt/legacy data must not hide all turns.
  if (!Array.isArray(fields) || fields.some(sessionTurnFieldError)) {
    return DEFAULT_SESSION_TURN_FIELDS;
  }
  return fields.map((field) =>
    field.kind === "metric"
      ? { kind: "metric", metric: field.metric }
      : { kind: "value", label: field.label, path: field.path },
  );
}

export function resolveSessionTurnFields(trace, fields) {
  return normalizeSessionTurnFields(fields)
    .filter((field) => field.kind === "value")
    .map((field) => {
      const parsed = parsePath(field.path, { columns: SESSION_TURN_COLUMNS });
      const root = trace?.[parsed.column];
      const result =
        root === undefined ? { found: false } : resolvePath(root, parsed.steps);
      return { ...field, ...result };
    });
}

export function mergeSessionTurnFields(config, fields) {
  return {
    ...config,
    display: {
      ...config?.display,
      sessionTurnFields: normalizeSessionTurnFields(fields),
    },
  };
}
