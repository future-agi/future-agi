import _ from "lodash";

// Structural keys that never represent a readable scalar param, so they are
// skipped from the params list (their contents are handled separately).
export const NON_PARAM_KEYS = new Set([
  "model",
  "model_detail",
  "tools",
  "providers",
  "reasoning",
  "booleans",
  "dropdowns",
  "id",
]);

const toRow = (key, value) => ({ heading: _.startCase(key), value });

// Coerce to a renderable primitive: null/undefined -> "-", objects (e.g. a
// response_format schema) -> their name/label, so nothing non-primitive
// reaches React as a child.
const toDisplayValue = (value) => {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return value.name ?? value.label ?? "-";
  return value;
};

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// A custom response schema is stored as its id; the schema name isn't in the
// snapshot, so show a short label instead of the raw uuid.
const formatResponseFormat = (value) =>
  typeof value === "string" && UUID_RE.test(value) ? "Custom" : value;

const getScalarRows = (config) => {
  const outputFormat = config.output_format;
  const hasOutputFormat = outputFormat !== null && outputFormat !== undefined;
  const isStringOutput = outputFormat === "string";
  const hideOutputFormat = hasOutputFormat && isStringOutput;
  const hideResponseFormat = hasOutputFormat && !isStringOutput;

  return Object.entries(config)
    .filter(([key, value]) => {
      if (NON_PARAM_KEYS.has(key)) return false;
      if (key === "output_format") return !hideOutputFormat;
      if (key === "response_format") return !hideResponseFormat;
      if (value === null || value === undefined) return true;
      if (typeof value !== "number" && typeof value !== "string") return false;
      // Drop blank rows (e.g. an unset tool_choice on tool-less versions).
      if (typeof value === "string" && value.trim() === "") return false;
      return true;
    })
    .map(([key, value]) => {
      let display = toDisplayValue(value);
      if (key === "response_format") display = formatResponseFormat(display);
      return toRow(key, display);
    });
};

const getMapRows = (map, formatValue) =>
  map && typeof map === "object"
    ? Object.entries(map).map(([key, value]) =>
        toRow(
          key,
          value === null || value === undefined ? "-" : formatValue(value),
        ),
      )
    : [];

// Reasoning is a nested object ({ sliders, dropdowns, showReasoningProcess }).
const getReasoningRows = (reasoning) => {
  if (!reasoning || typeof reasoning !== "object") return [];
  const rows = [
    ...getMapRows(reasoning.sliders, (value) => value),
    ...getMapRows(reasoning.dropdowns, (value) => _.startCase(String(value))),
  ];
  const { showReasoningProcess } = reasoning;
  if (showReasoningProcess !== null && showReasoningProcess !== undefined) {
    rows.push({
      heading: "Show Reasoning Process",
      value: String(showReasoningProcess),
    });
  }
  return rows;
};

// Full ordered row list for the params hover panel.
export const buildParameterRows = (config) => {
  if (!config) return [];
  return [
    ...getScalarRows(config),
    ...getMapRows(config.booleans, String),
    ...getMapRows(config.dropdowns, (value) => _.startCase(String(value))),
    ...getReasoningRows(config.reasoning),
  ];
};
