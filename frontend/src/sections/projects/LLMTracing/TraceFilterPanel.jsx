/* eslint-disable react/prop-types */
/**
 * TraceFilterPanel — trace-specific filter with:
 *   - AI input (shared)
 *   - Basic tab: dashboard-style property picker + checkbox value picker
 *   - Query tab: inline token builder (shared FilterPanel's QueryInput)
 */
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  ClickAwayListener,
  Divider,
  IconButton,
  InputAdornment,
  MenuItem,
  Paper,
  Popper,
  Popover,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import axios, { endpoints } from "src/utils/axios";
import { useDashboardFilterValues } from "src/hooks/useDashboards";
import { useDebounce } from "src/hooks/use-debounce";
import { useAIFilter } from "src/hooks/use-ai-filter";
import { QueryInput } from "src/components/filter-panel";
import {
  getPickerOptionExactMatches,
  getPickerOptionLabel,
  getPickerOptionSearchText,
  getPickerOptionSecondaryLabel,
  getPickerOptionValue,
  usesFreeTextValue,
} from "./filterValuePickerUtils";
import { ID_ONLY_FIELDS } from "./idFields";

// ---------------------------------------------------------------------------
// Trace filter fields (for Query tab via shared FilterPanel)
// ---------------------------------------------------------------------------
const BASE_TRACE_FILTER_FIELDS = [
  { value: "name", label: "Trace Name", type: "string" },
  {
    value: "status",
    label: "Status",
    type: "enum",
    choices: ["OK", "ERROR", "UNSET"],
  },
  { value: "model", label: "Model", type: "string" },
  {
    value: "node_type",
    label: "Node Type",
    type: "enum",
    choices: [
      "chain",
      "retriever",
      "generation",
      "llm",
      "tool",
      "agent",
      "embedding",
    ],
  },
  { value: "service_name", label: "Service Name", type: "string" },
  { value: "provider", label: "Provider", type: "string" },
  { value: "tag", label: "Tag", type: "string" },
];

const TRACE_ID_FIELD = {
  value: "trace_id",
  label: "Trace ID",
  type: "string",
};

const SPAN_ID_FIELD = {
  value: "span_id",
  label: "Span ID",
  type: "string",
};

// Prepend id filters based on which LLM Tracing tab the filter panel
// renders in:
//   `tab` === "trace"  → Trace ID
//   `tab` === "spans"  → Trace ID + Span ID
//   otherwise          → no id fields (preserves behavior for non-LLMTracing
//                        consumers such as sessions/users).
// Exported for direct unit testing.
export const getTraceFilterFields = (tab) => {
  if (tab === "trace") return [TRACE_ID_FIELD, ...BASE_TRACE_FILTER_FIELDS];
  if (tab === "spans")
    return [TRACE_ID_FIELD, SPAN_ID_FIELD, ...BASE_TRACE_FILTER_FIELDS];
  return BASE_TRACE_FILTER_FIELDS;
};

// Map a static trace field to a picker property. In spans view the root
// "Trace Name" field is reused as "Span Name" — remap its id so the picker
// fires a distinct span_name request instead of duplicating the name request.
export const toStaticFilterProperty = (field, isSpansView = false) => {
  if (isSpansView && field.value === "name") {
    return {
      id: "span_name",
      name: "Span Name",
      category: "system",
      type: "string",
      apiColType: "SYSTEM_METRIC",
    };
  }
  return {
    id: field.value,
    name: field.label,
    category: "system",
    // Pinned so the eval-task wire encoding doesn't have to guess
    // from `category` alone — without this every static field would
    // round-trip through the chain with apiColType=undefined and
    // get coerced to SPAN_ATTRIBUTE downstream.
    apiColType: "SYSTEM_METRIC",
    type: field.type === "enum" ? "string" : field.type,
    ...(field.choices ? { choices: field.choices } : {}),
  };
};

// ---------------------------------------------------------------------------
// Category config for dashboard-style property picker
// ---------------------------------------------------------------------------
const CATEGORIES = [
  { key: "all", label: "All", icon: "mdi:view-grid-outline" },
  { key: "system", label: "System", icon: "mdi:tune-variant" },
  { key: "eval", label: "Evals", icon: "mdi:check-circle-outline" },
  { key: "annotation", label: "Annotations", icon: "mdi:comment-text-outline" },
  { key: "attribute", label: "Attributes", icon: "mdi:code-braces" },
];

function mapCategory(raw) {
  if (!raw) return "system";
  if (raw.includes("eval")) return "eval";
  if (raw.includes("annotation")) return "annotation";
  if (raw.includes("custom") || raw.includes("attribute")) return "attribute";
  return "system";
}

// `value` is the canonical backend op name; `label` is the dropdown text.
// For strings/text: "equals"/"not equals" send `in`/`not_in` (`IN (x)` ≡ `= x`).
const STRING_OPS = [
  { value: "in", label: "equals" },
  { value: "not_in", label: "not equals" },
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "not contains" },
  { value: "starts_with", label: "starts with" },
  { value: "ends_with", label: "ends with" },
  { value: "is_null", label: "is null" },
  { value: "is_not_null", label: "is not null" },
];

const NUMBER_OPS = [
  { value: "equals", label: "equals" },
  { value: "not_equals", label: "not equals" },
  { value: "greater_than", label: "greater than" },
  { value: "greater_than_or_equal", label: "greater than or equals" },
  { value: "less_than", label: "less than" },
  { value: "less_than_or_equal", label: "less than or equals" },
  { value: "between", label: "between", range: true },
  { value: "not_between", label: "not between", range: true },
  { value: "is_null", label: "is null" },
  { value: "is_not_null", label: "is not null" },
];

const DATE_OPS = [
  { value: "less_than", label: "before" },
  { value: "greater_than", label: "after" },
  { value: "equals", label: "on" },
  { value: "not_equals", label: "not on" },
  { value: "greater_than_or_equal", label: "on or after" },
  { value: "less_than_or_equal", label: "on or before" },
  { value: "between", label: "between", range: true },
  { value: "not_between", label: "not between", range: true },
  { value: "is_null", label: "is null" },
  { value: "is_not_null", label: "is not null" },
];

const BOOLEAN_OPS = [
  { value: "equals", label: "equals" },
  { value: "not_equals", label: "not equals" },
  { value: "is_null", label: "is null" },
  { value: "is_not_null", label: "is not null" },
];

// thumbs_up_down annotations: 2 fixed display choices ("Thumbs Up"/"Thumbs Down").
// Distinct from CATEGORICAL_OPS — we don't expose contains/not_contains for a
// 2-value enum.
const THUMBS_OPS = [
  { value: "equals", label: "is" },
  { value: "not_equals", label: "is not" },
  { value: "is_null", label: "is null" },
  { value: "is_not_null", label: "is not null" },
];

const ANNOTATOR_OPS = [
  { value: "equals", label: "is" },
  { value: "not_equals", label: "is not" },
  { value: "is_null", label: "is null" },
  { value: "is_not_null", label: "is not null" },
];

// Direct UUID identifiers support exact multi-select through the canonical
// list operators. Avoid substring/null operators for these fields.
const ID_ONLY_OPS = [
  { value: "in", label: "equals" },
  { value: "not_in", label: "not equals" },
];

const ARRAY_OPS = [
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "not contains" },
  { value: "is_null", label: "is empty" },
  { value: "is_not_null", label: "is not empty" },
];

const CATEGORICAL_OPS = [
  { value: "equals", label: "is" },
  { value: "not_equals", label: "is not" },
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "not contains" },
  { value: "is_null", label: "is null" },
  { value: "is_not_null", label: "is not null" },
];

const TEXT_OPS = [
  { value: "in", label: "equals" },
  { value: "not_in", label: "not equals" },
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "not contains" },
  { value: "starts_with", label: "starts with" },
  { value: "ends_with", label: "ends with" },
  { value: "is_null", label: "is null" },
  { value: "is_not_null", label: "is not null" },
];

// Identity maps; kept for the QueryInput integration call sites.
const QUERY_TO_BASIC_OP = {
  equals: "equals",
  not_equals: "not_equals",
  starts_with: "starts_with",
};

const BASIC_TO_QUERY_OP = {
  equals: "equals",
  not_equals: "not_equals",
  starts_with: "starts_with",
};

const NUMERIC_TYPES = new Set([
  "number",
  "float",
  "integer",
  "int",
  "decimal",
  "double",
  "numeric",
  "long",
]);

const DATE_TYPES = new Set(["date", "datetime", "timestamp"]);
const BOOLEAN_TYPES = new Set(["boolean", "bool"]);
const ARRAY_TYPES = new Set(["array", "list", "json"]);

const normalizeFieldType = (rawType) => {
  if (!rawType) return "string";
  const t = String(rawType).toLowerCase();
  if (NUMERIC_TYPES.has(t)) return "number";
  if (DATE_TYPES.has(t)) return "date";
  if (BOOLEAN_TYPES.has(t)) return "boolean";
  if (ARRAY_TYPES.has(t)) return "array";
  return "string";
};

export const isValidNumericInput = (v) => {
  if (v === "" || v === undefined || v === null) return true;
  return /^-?\d*\.?\d*$/.test(String(v).trim());
};

// Empty values pass — computeValidFilters already drops empty rows before apply,
// so this only guards against partial inputs like "-" or "1.5.6" leaking through.
export const isCompleteNumericValue = (v) => {
  if (v === undefined || v === null) return true;
  const str = String(v).trim();
  if (str === "") return true;
  if (!/^-?(\d+\.?\d*|\.\d+)$/.test(str)) return false;
  return Number.isFinite(parseFloat(str));
};

const NUMERIC_HELPER_TEXT_PROPS = {
  sx: {
    fontSize: 10,
    mx: 0.5,
    mt: 0,
    position: "absolute",
    top: "100%",
    left: 0,
    lineHeight: 1.2,
    whiteSpace: "nowrap",
  },
};

const NUMERIC_TEXTFIELD_SX = {
  flex: "1 1 80px",
  minWidth: 0,
  position: "relative",
};

const getOperators = (fieldType) => {
  if (fieldType === "categorical") return CATEGORICAL_OPS;
  if (fieldType === "thumbs") return THUMBS_OPS;
  if (fieldType === "annotator") return ANNOTATOR_OPS;
  if (fieldType === "text") return TEXT_OPS;
  const t = normalizeFieldType(fieldType);
  if (t === "number") return NUMBER_OPS;
  if (t === "date") return DATE_OPS;
  if (t === "boolean") return BOOLEAN_OPS;
  if (t === "array") return ARRAY_OPS;
  return STRING_OPS;
};

// Wrapper that special-cases ID-only fields. Use from FilterRow + apply
// validation; keep `getOperators` as the pure type → ops mapping (Query
// tab + AI filter schema rely on the type-only behavior).
const getOperatorsForFilter = (filter) => {
  if (filter?.field && ID_ONLY_FIELDS.has(filter.field)) return ID_ONLY_OPS;
  return getOperators(filter?.fieldType);
};

const getDefaultOperatorForFilter = (filter, ops) => {
  const defaultOp =
    DEFAULT_OP_FOR_TYPE[filter?.fieldType] ||
    DEFAULT_OP_FOR_TYPE[normalizeFieldType(filter?.fieldType)] ||
    "equals";
  return ops.some((op) => op.value === defaultOp)
    ? defaultOp
    : ops[0]?.value || "equals";
};

const getEquivalentPanelOperator = (operator) => {
  if (operator === "in") return "equals";
  if (operator === "not_in") return "not_equals";
  return operator;
};

export const normalizeFilterRowOperator = (filter) => {
  const ops = getOperatorsForFilter(filter);
  if (ops.some((op) => op.value === filter?.operator)) return filter;

  const equivalentOperator = getEquivalentPanelOperator(filter?.operator);
  const operator = ops.some((op) => op.value === equivalentOperator)
    ? equivalentOperator
    : getDefaultOperatorForFilter(filter, ops);
  return { ...filter, operator };
};

const DEFAULT_OP_FOR_TYPE = {
  number: "equals",
  date: "equals",
  boolean: "equals",
  array: "contains",
  string: "in",
  categorical: "equals",
  thumbs: "equals",
  text: "in",
  annotator: "equals",
};

// String equality uses the list picker so single and multi-value filters share
// the same canonical `in` / `not_in` API shape.
const HYDRATE_STRING_OP = { equals: "in", not_equals: "not_in" };

// Categorical / thumbs ops in saved views — reverse the save-side LEGACY_OP_ALIAS so the menu renders.
const HYDRATE_CATEGORICAL_OP = { equals: "is", not_equals: "is_not" };

const NO_VALUE_OPS = new Set(["is_null", "is_not_null"]);

// Build the list of *valid, applyable* filter rows: a row needs a field and,
// unless its operator takes no value, a non-empty value. Returns null when
// nothing is applyable. Shared by the debounced auto-apply and the
// flush-on-close path so both compute the filter set identically.
const computeValidFilters = (rows) => {
  const valid = rows.map(normalizeFilterRowOperator).filter((r) => {
    if (!r.field) return false;
    if (NO_VALUE_OPS.has(r.operator)) return true;
    const ops = getOperatorsForFilter(r);
    const opDef = ops.find((o) => o.value === r.operator);
    if (opDef?.range)
      return Array.isArray(r.value) && r.value[0] !== "" && r.value[1] !== "";
    if (Array.isArray(r.value)) return r.value.length > 0;
    return r.value !== "" && r.value !== undefined && r.value !== null;
  });
  return valid.length ? valid : null;
};

// Canonical projection for dedup: two filter sets that produce the same API
// query serialize identically regardless of row key order or display-only
// fields (fieldName/fieldCategory can differ between the open/AI/apply init
// paths), so an identical set never fires a redundant request.
export const serializeFilterSet = (filters) =>
  JSON.stringify(
    (filters || []).map((f) => ({
      field: f.field,
      operator: normalizeFilterRowOperator(f).operator,
      value: f.value,
    })),
  );

// Hold auto-apply while a numeric row is mid-edit: a partial/invalid value
// ("-", "1.5.6"), or a range with only one bound filled. Mirrors the old
// disabled-Apply gate (TH-5195) so invalid numbers never reach the API and a
// half-filled range doesn't drop the already-applied filter and refire.
export const hasIncompleteNumericRow = (rows) =>
  rows.some((r) => {
    if (normalizeFieldType(r.fieldType) !== "number") return false;
    if (Array.isArray(r.value)) {
      const filled = r.value.filter(
        (v) => v !== "" && v !== undefined && v !== null,
      );
      if (r.value.length >= 2 && filled.length === 1) return true;
      return filled.some((v) => !isCompleteNumericValue(v));
    }
    return (
      r.value !== "" &&
      r.value !== undefined &&
      r.value !== null &&
      !isCompleteNumericValue(r.value)
    );
  });

// Scalar ops — value picker forces single-select. Multi-value goes via in/not_in.
const SINGLE_VALUE_OPS = new Set([
  "equals",
  "not_equals",
  "contains",
  "not_contains",
  "starts_with",
  "ends_with",
]);

// List ops — multi-select picker.
const LIST_VALUE_OPS = new Set(["in", "not_in"]);

// ---------------------------------------------------------------------------
// Hook: fetch properties from dashboard metrics
// ---------------------------------------------------------------------------
// System metrics to exclude — only the ones that are aggregate counts or
// meta-fields with no per-trace value worth filtering on. Numeric metrics
// like latency/tokens/cost ARE useful as rule and dashboard filters and
// should stay in the picker.
const EXCLUDED_METRICS = new Set([
  "project",
  "session_count",
  "user_count",
  "trace_count",
  "span_count",
  "dataset",
  "eval_source",
  "row_count",
  "cell_error_rate",
  // duplicate of node_type — both map to observation_type
  "span_kind",
]);
const PROPERTY_PICKER_RENDER_LIMIT = 250;

const normalizePropertySearchText = (value) =>
  String(value || "")
    .toLowerCase()
    .replace(/[_\-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

export function filterPropertiesForPicker({
  properties,
  category = "all",
  search = "",
  hasCategorySidebar = true,
}) {
  const query = normalizePropertySearchText(search);
  let list = properties || [];
  if (hasCategorySidebar && category !== "all") {
    list = list.filter((property) => property.category === category);
  }
  if (!query) return list;
  return list.filter((property) => {
    const name = normalizePropertySearchText(property.name);
    const id = normalizePropertySearchText(property.id);
    return name.includes(query) || id.includes(query);
  });
}

const resolveFieldCategory = (explicitCategory, prop, fallback = "system") => {
  if (explicitCategory !== undefined) return explicitCategory;
  if (prop) return prop.category;
  return fallback;
};

const ANNOTATOR_FILTER_PROPERTY = {
  id: "annotator",
  name: "Annotator",
  category: "annotation",
  rawCategory: "annotation_metric",
  type: "annotator",
  // This is visually grouped with annotation filters, but the backend treats
  // column_id=annotator as a global Score annotator filter, not a label column.
  apiColType: "SYSTEM_METRIC",
  allowCustomValue: false,
};

function metricToTraceFilterProperty(m) {
  const outputType = m.outputType || m.output_type;
  // Eval metrics don't carry a `type` field; derive the filter input type from
  // `output_type`. SCORE → number (slider), PASS_FAIL/CHOICE/CHOICES → string
  // (dropdown of choices).
  const isEval = m.category === "eval_metric" || m.category === "evalMetric";
  const isAnnotation =
    m.category === "annotation_metric" || m.category === "annotationMetric";
  let type;
  if (isEval && outputType) {
    const ot = String(outputType).toUpperCase();
    if (ot === "SCORE") type = "number";
    else type = "string";
  } else if (isAnnotation && outputType) {
    const ot = String(outputType).toLowerCase();
    if (ot === "numeric" || ot === "star") type = "number";
    else if (ot === "text") type = "text";
    else if (ot === "thumbs_up_down") type = "thumbs";
    else type = "categorical";
  } else {
    type = normalizeFieldType(m.type);
  }
  // thumbs labels have two fixed choices — surface them so the value picker
  // renders a multi-select without needing a dashboard lookup.
  const choices = type === "thumbs" ? ["Thumbs Up", "Thumbs Down"] : m.choices;
  const apiColType = isEval
    ? "EVAL_METRIC"
    : isAnnotation
      ? "ANNOTATION"
      : m.category === "system_metric" || m.category === "systemMetric"
        ? "SYSTEM_METRIC"
        : "SPAN_ATTRIBUTE";
  return {
    id: m.name,
    name: m.displayName || m.display_name || m.name,
    category: mapCategory(m.category),
    rawCategory: m.category,
    type,
    outputType,
    choices,
    apiColType,
  };
}

export function buildTraceFilterProperties(
  metrics,
  { isSimulator = false, sourceScope = null } = {},
) {
  const properties = metrics
    .filter((m) => {
      const name = m.name;
      const cat = m.category;
      const src = m.source;
      const sources = Array.isArray(m.sources) ? m.sources : [];
      const isSpanOnly =
        (src === "spans" || sources.includes("spans")) &&
        src !== "all" &&
        src !== "both" &&
        !sources.includes("all") &&
        !sources.includes("both") &&
        !sources.includes("traces");
      const isSimulationMetric =
        src === "simulation" || sources.includes("simulation");

      // Always exclude blacklisted metrics
      if (EXCLUDED_METRICS.has(name)) return false;

      // Exclude dataset-only metrics
      if (src === "datasets") return false;

      if (sourceScope === "simulation" && !isSimulationMetric) return false;

      // Span-only metrics are only available when the panel is bound to span
      // rows. Trace/session/user panels should not render span row columns.
      if (isSpanOnly && sourceScope !== "spans") return false;

      // Exclude simulation metrics for non-simulator projects
      if (src === "simulation" && !isSimulator) return false;

      // Exclude custom_column (dataset columns)
      if (cat === "custom_column" || cat === "customColumn") return false;

      // System metrics: string and number types
      if (cat === "system_metric" || cat === "systemMetric") {
        const normalized = normalizeFieldType(m.type);
        return normalized === "string" || normalized === "number";
      }

      // Evals, annotations, custom attributes — include
      if (cat === "eval_metric" || cat === "evalMetric") return true;
      if (cat === "annotation_metric" || cat === "annotationMetric")
        return true;
      if (cat === "custom_attribute" || cat === "customAttribute") return true;

      return false;
    })
    .map(metricToTraceFilterProperty);

  const firstAnnotationIndex = properties.findIndex(
    (property) => property.category === "annotation",
  );
  const alreadyHasAnnotator = properties.some(
    (property) => property.id === ANNOTATOR_FILTER_PROPERTY.id,
  );

  if (firstAnnotationIndex !== -1 && !alreadyHasAnnotator) {
    properties.splice(firstAnnotationIndex, 0, ANNOTATOR_FILTER_PROPERTY);
  }

  return properties;
}

export function useTraceFilterProperties(
  projectId,
  { enabled = true, isSimulator = false, sourceScope = null } = {},
) {
  return useQuery({
    queryKey: [
      "trace-filter-properties-v2",
      projectId,
      isSimulator,
      sourceScope,
    ],
    enabled: enabled && Boolean(projectId),
    queryFn: async () => {
      const params = {};
      if (projectId) params.project_ids = projectId;
      // Observe filter dropdown wants per-CustomEvalConfig eval entries (so
      // the dropdown matches the per-config columns in the trace/span list
      // table). Default behaviour at /tracer/dashboard/metrics/ is still
      // template-level — used by dashboards, PrimaryGraph, widget pickers.
      params.per_eval_config = true;
      const { data } = await axios.get(endpoints.dashboard.metrics, { params });
      return data?.result?.metrics || [];
    },
    select: (metrics) =>
      buildTraceFilterProperties(metrics, { isSimulator, sourceScope }),
    staleTime: 5 * 60_000,
    gcTime: 15 * 60_000,
  });
}

// ---------------------------------------------------------------------------
// PropertyPicker — dashboard-style two-column picker
// ---------------------------------------------------------------------------
function PropertyPicker({
  anchorEl,
  open,
  onClose,
  properties,
  onSelect,
  categories = CATEGORIES,
}) {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const hasCategorySidebar = categories && categories.length > 0;

  const filtered = useMemo(
    () =>
      filterPropertiesForPicker({
        properties,
        category,
        search,
        hasCategorySidebar,
      }),
    [properties, category, search, hasCategorySidebar],
  );

  const counts = useMemo(() => {
    const c = { all: properties.length };
    for (const p of properties) c[p.category] = (c[p.category] || 0) + 1;
    return c;
  }, [properties]);
  const visibleProperties = filtered.slice(0, PROPERTY_PICKER_RENDER_LIMIT);
  const hiddenCount = Math.max(
    filtered.length - PROPERTY_PICKER_RENDER_LIMIT,
    0,
  );

  const paperWidth = hasCategorySidebar ? 480 : 320;

  return (
    <Popper
      open={open}
      anchorEl={anchorEl}
      placement="bottom-start"
      sx={{ zIndex: 1400 }}
    >
      <ClickAwayListener onClickAway={onClose}>
        <Paper
          elevation={8}
          sx={{
            width: paperWidth,
            maxHeight: 380,
            display: "flex",
            flexDirection: "column",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 2,
          }}
        >
          <Box sx={{ p: 1.5 }}>
            <TextField
              size="small"
              fullWidth
              placeholder="Search properties..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              autoFocus
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon="eva:search-fill"
                      width={16}
                      sx={{ color: "text.disabled" }}
                    />
                  </InputAdornment>
                ),
                endAdornment: filtered.length > 0 && (
                  <InputAdornment position="end">
                    <Typography
                      variant="caption"
                      sx={{ color: "text.disabled", fontSize: 11 }}
                    >
                      {filtered.length}
                    </Typography>
                  </InputAdornment>
                ),
                sx: { fontSize: 13 },
              }}
            />
          </Box>
          <Divider />
          <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
            {hasCategorySidebar && (
              <Box
                sx={{
                  width: 130,
                  borderRight: "1px solid",
                  borderColor: "divider",
                  overflow: "auto",
                  py: 0.5,
                }}
              >
                {categories.map((cat) => (
                  <Box
                    key={cat.key}
                    onClick={() => setCategory(cat.key)}
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 0.75,
                      px: 1.25,
                      py: 0.5,
                      cursor: "pointer",
                      borderRadius: 1,
                      mx: 0.5,
                      bgcolor:
                        category === cat.key
                          ? "action.selected"
                          : "transparent",
                      "&:hover": {
                        bgcolor:
                          category === cat.key
                            ? "action.selected"
                            : "action.hover",
                      },
                    }}
                  >
                    <Iconify
                      icon={cat.icon}
                      width={14}
                      sx={{
                        color:
                          category === cat.key
                            ? "primary.main"
                            : "text.secondary",
                      }}
                    />
                    <Typography
                      sx={{
                        fontSize: 12,
                        fontWeight: category === cat.key ? 600 : 400,
                        color:
                          category === cat.key
                            ? "text.primary"
                            : "text.secondary",
                        flex: 1,
                      }}
                    >
                      {cat.label}
                    </Typography>
                    {counts[cat.key] > 0 && (
                      <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
                        {counts[cat.key]}
                      </Typography>
                    )}
                  </Box>
                ))}
              </Box>
            )}
            <Box sx={{ flex: 1, overflow: "auto", maxHeight: 280 }}>
              {filtered.length === 0 && (
                <Typography
                  sx={{
                    p: 2,
                    textAlign: "center",
                    fontSize: 12,
                    color: "text.disabled",
                  }}
                >
                  No properties found
                </Typography>
              )}
              {visibleProperties.map((prop, idx) => (
                <Box
                  key={`${prop.category}:${prop.id}:${idx}`}
                  data-filter-property-option={prop.id}
                  data-filter-property-label={prop.name}
                  onClick={() => {
                    onSelect(prop);
                    onClose();
                    setSearch("");
                    setCategory("all");
                  }}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    px: 1.5,
                    py: 0.6,
                    cursor: "pointer",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Typography
                    noWrap
                    sx={{
                      fontSize: 13,
                      flex: 1,
                      maxWidth: 250,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {prop.name}
                  </Typography>
                  {prop.outputType && (
                    <Chip
                      size="small"
                      variant="outlined"
                      label={
                        prop.outputType === "SCORE"
                          ? "score"
                          : prop.outputType === "PASS_FAIL"
                            ? "P/F"
                            : prop.outputType
                      }
                      sx={{ height: 18, fontSize: 10, flexShrink: 0 }}
                    />
                  )}
                  {hasCategorySidebar && prop.category && (
                    <Chip
                      size="small"
                      variant="outlined"
                      label={prop.category}
                      sx={{
                        height: 16,
                        fontSize: 9,
                        flexShrink: 0,
                        textTransform: "capitalize",
                      }}
                    />
                  )}
                </Box>
              ))}
              {hiddenCount > 0 && (
                <Typography
                  sx={{
                    px: 1.5,
                    py: 1,
                    fontSize: 11,
                    color: "text.secondary",
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  {hiddenCount} more properties. Search to narrow the list.
                </Typography>
              )}
            </Box>
          </Box>
        </Paper>
      </ClickAwayListener>
    </Popper>
  );
}

// ---------------------------------------------------------------------------
// ValuePicker — checkbox multi-select dropdown
// ---------------------------------------------------------------------------
// Session-specific fields that have their own value endpoint
const SESSION_VALUE_FIELDS = new Set([
  "session_id",
  "user_id",
  "first_message",
  "last_message",
]);

const FREE_TEXT_NO_OPTIONS_TEXT = "No suggestions yet — type a value to add it";

function normalizePickerValues(values) {
  const rawValues = Array.isArray(values) ? values : values ? [values] : [];
  const cleanValues = rawValues
    .map((item) => String(getPickerOptionValue(item)).trim())
    .filter(Boolean);
  return Array.from(new Set(cleanValues));
}

function ValuePicker({
  propertyId,
  propertyCategory,
  projectId,
  value = [],
  onChange,
  source = "traces",
  property,
  singleSelect = false,
}) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 500);

  // If the property declares its own static choices (e.g. the Project filter
  // on the cross-project user-detail page), use them directly. Skips both
  // the dashboard lookup and the session fallback — useful when the field is
  // not indexed by the dashboard metrics pipeline or when options are known
  // client-side.
  const hasStaticChoices =
    propertyCategory !== "annotation" &&
    Array.isArray(property?.choices) &&
    property.choices.length > 0;

  const metricType = (() => {
    if (propertyCategory === "system") return "system_metric";
    if (propertyCategory === "eval") return "eval_metric";
    if (propertyCategory === "annotation") return "annotation_metric";
    if (propertyCategory === "attribute") return "custom_attribute";
    return "system_metric";
  })();

  const isSessionField =
    !hasStaticChoices && SESSION_VALUE_FIELDS.has(propertyId);

  const isIdOnlyField = !hasStaticChoices && ID_ONLY_FIELDS.has(propertyId);

  // Primary: dashboard API values
  const {
    data: dashboardOptions = [],
    isLoading: dashLoading,
    isError: dashError,
  } = useDashboardFilterValues({
    metricName: propertyId,
    metricType,
    projectIds: projectId ? [projectId] : [],
    source,
    search: isIdOnlyField ? debouncedSearch : "",
    enabled:
      !hasStaticChoices &&
      Boolean(anchorEl) &&
      (!isIdOnlyField || Boolean(debouncedSearch)),
  });

  // Fallback: session filter values endpoint (for session-specific fields)
  const { data: sessionOptions = [], isLoading: sessionLoading } = useQuery({
    queryKey: ["session-filter-values", projectId, propertyId, debouncedSearch],
    queryFn: () =>
      axios.get(endpoints.project.getSessionFilterValues(), {
        params: {
          project_id: projectId,
          column: propertyId,
          search: debouncedSearch || undefined,
          page: 0,
          page_size: 100,
        },
      }),
    select: (res) => res.data?.result?.values || [],
    enabled:
      !hasStaticChoices && isSessionField && !!projectId && Boolean(anchorEl),
    staleTime: 30_000,
  });

  // Source: static choices > session endpoint > dashboard API
  const options = hasStaticChoices
    ? property.choices
    : isSessionField
      ? sessionOptions
      : dashboardOptions;
  const isLoading = hasStaticChoices
    ? false
    : isSessionField
      ? sessionLoading
      : dashLoading;
  const isError = !hasStaticChoices && !isSessionField && dashError;

  const filtered = useMemo(() => {
    if (!search || isSessionField || isIdOnlyField) return options;
    const q = search.toLowerCase();
    return options.filter((o) =>
      getPickerOptionSearchText(o).toLowerCase().includes(q),
    );
  }, [options, search, isSessionField, isIdOnlyField]);

  const selectedValues = useMemo(() => normalizePickerValues(value), [value]);

  const toggleValue = useCallback(
    (val) => {
      // Use the shared helper to read the picker option's stable value
      // (handles both string and {value, label} object shapes).
      const strVal = getPickerOptionValue(val);
      if (singleSelect) {
        // Clicking the already-selected value clears; clicking a different
        // value replaces — standard single-select dropdown UX.
        onChange(value.includes(strVal) ? [] : [strVal]);
        return;
      }
      onChange(
        selectedValues.includes(strVal)
          ? selectedValues.filter((v) => v !== strVal)
          : [...selectedValues, strVal],
      );
    },
    [selectedValues, value, onChange, singleSelect],
  );

  const customSearchValue = search.trim();
  const searchMatchesExistingOption = options.some((option) =>
    getPickerOptionExactMatches(option).some(
      (matchValue) =>
        matchValue.toLowerCase() === customSearchValue.toLowerCase(),
    ),
  );
  const showCustomValueRow = Boolean(
    property?.allowCustomValue !== false &&
      customSearchValue &&
      !searchMatchesExistingOption,
  );

  return (
    <>
      <Box
        data-filter-value-trigger={property?.id || ""}
        onClick={(e) => setAnchorEl(e.currentTarget)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          flexWrap: "wrap",
          minHeight: 28,
          minWidth: 0,
          flex: "1 1 180px",
          maxWidth: "100%",
          px: 1,
          py: 0.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          cursor: "pointer",
          "&:hover": { borderColor: "text.disabled" },
        }}
      >
        {selectedValues.length === 0 ? (
          <Typography sx={{ fontSize: 12, color: "text.disabled", flex: 1 }}>
            {isLoading
              ? "Loading..."
              : options.length === 0
                ? singleSelect
                  ? "No value available"
                  : "No values available"
                : singleSelect
                  ? "Select a value..."
                  : "Select values..."}
          </Typography>
        ) : singleSelect ? (
          // Plain text instead of a chip — chips read as "removable token
          // in a list", which mis-signals multi-select.
          (() => {
            const v = selectedValues[0];
            const match = options.find((o) => {
              const ov = typeof o === "string" ? o : o.value;
              return ov === v;
            });
            const displayLabel =
              (typeof match === "string" ? match : match?.label) || v;
            return (
              <Typography
                key={v}
                noWrap
                title={displayLabel}
                sx={{
                  fontSize: 12,
                  color: "text.primary",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  minWidth: 0,
                  flex: 1,
                }}
              >
                {displayLabel}
              </Typography>
            );
          })()
        ) : (
          selectedValues.slice(0, 3).map((v) => {
            const match = options.find((o) => {
              const ov = typeof o === "string" ? o : o.value;
              return ov === v;
            });
            const displayLabel =
              (typeof match === "string" ? match : match?.label) || v;
            const secondaryLabel = getPickerOptionSecondaryLabel(match);
            const chipTitle = secondaryLabel
              ? `${displayLabel} (${secondaryLabel})`
              : displayLabel;
            return (
              <Chip
                key={v}
                label={displayLabel}
                title={chipTitle}
                size="small"
                onDelete={(e) => {
                  e.stopPropagation();
                  onChange(selectedValues.filter((x) => x !== v));
                }}
                deleteIcon={<Iconify icon="mdi:close" width={10} />}
                sx={{
                  height: 20,
                  fontSize: 10,
                  maxWidth: 70,
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            );
          })
        )}
        {!singleSelect && selectedValues.length > 3 && (
          <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
            +{selectedValues.length - 3}
          </Typography>
        )}
        <Iconify
          icon={anchorEl ? "mdi:chevron-up" : "mdi:chevron-down"}
          width={14}
          sx={{ color: "text.disabled", ml: "auto", flexShrink: 0 }}
        />
      </Box>

      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={() => {
          setAnchorEl(null);
          setSearch("");
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: { width: { xs: 280, sm: 320 }, borderRadius: "8px", mt: 0.5 },
          },
        }}
      >
        <Box sx={{ p: 1 }}>
          <TextField
            size="small"
            fullWidth
            placeholder="Search values..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="mdi:magnify"
                    width={14}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
              sx: { fontSize: 12, height: 30 },
            }}
          />
          <Typography
            sx={{ fontSize: 10, color: "text.disabled", mt: 0.5, px: 0.25 }}
          >
            {singleSelect
              ? "Select a single value"
              : "Select one or more values (multi-select)"}
          </Typography>
        </Box>
        <Divider />
        <Box sx={{ maxHeight: 220, overflow: "auto" }}>
          {isLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <CircularProgress size={16} />
            </Box>
          )}
          {!isLoading && !search && (isError || filtered.length === 0) && (
            <Typography
              sx={{
                p: 1.5,
                textAlign: "center",
                fontSize: 12,
                color: "text.disabled",
              }}
            >
              {isError
                ? "Values not available for this property"
                : FREE_TEXT_NO_OPTIONS_TEXT}
            </Typography>
          )}
          {/* Custom-value row is rendered below in the showCustomValueRow
              block — keeps a single source of truth for the "Specify"
              fallback (search did not match any fetched option). */}
          {filtered.map((opt) => {
            const strVal = getPickerOptionValue(opt);
            const label = getPickerOptionLabel(opt);
            const secondaryLabel = getPickerOptionSecondaryLabel(opt);
            const isSelected = selectedValues.includes(strVal);
            return (
              <Box
                key={strVal}
                data-filter-value-option={strVal}
                onClick={() => toggleValue(opt)}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  px: 1.5,
                  py: secondaryLabel ? 0.65 : 0.75,
                  cursor: "pointer",
                  bgcolor: isSelected ? "action.selected" : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify
                  icon={
                    singleSelect
                      ? isSelected
                        ? "mdi:radiobox-marked"
                        : "mdi:radiobox-blank"
                      : isSelected
                        ? "mdi:checkbox-marked"
                        : "mdi:checkbox-blank-outline"
                  }
                  width={18}
                  sx={{
                    color: isSelected ? "primary.main" : "text.secondary",
                    flexShrink: 0,
                  }}
                />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography
                    noWrap
                    title={label}
                    sx={{
                      fontSize: 12,
                      fontWeight: isSelected ? 600 : 400,
                      color: "text.primary",
                    }}
                  >
                    {label}
                  </Typography>
                  {secondaryLabel && (
                    <Typography
                      noWrap
                      title={secondaryLabel}
                      sx={{ fontSize: 10, color: "text.secondary", mt: 0.1 }}
                    >
                      {secondaryLabel}
                    </Typography>
                  )}
                </Box>
              </Box>
            );
          })}
          {showCustomValueRow && (
            <>
              {filtered.length > 0 && <Divider />}
              <Box
                data-filter-value-option={customSearchValue}
                onClick={() => {
                  // singleSelect: replace the selection. Otherwise: append
                  // (but skip if the value is already selected).
                  if (singleSelect) {
                    onChange([customSearchValue]);
                  } else if (!selectedValues.includes(customSearchValue)) {
                    onChange([...selectedValues, customSearchValue]);
                  }
                  setSearch("");
                }}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  px: 1.5,
                  py: 0.75,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify
                  icon="mdi:plus-circle-outline"
                  width={18}
                  sx={{
                    color: "primary.main",
                    flexShrink: 0,
                  }}
                />
                <Typography sx={{ fontSize: 12 }}>
                  + Specify: <strong>{customSearchValue}</strong>
                </Typography>
              </Box>
            </>
          )}
        </Box>
        {selectedValues.length > 0 && (
          <>
            <Divider />
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                px: 1.5,
                py: 0.75,
              }}
            >
              <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
                {selectedValues.length} selected
              </Typography>
              <Button
                size="small"
                onClick={() => onChange([])}
                sx={{
                  textTransform: "none",
                  fontSize: 11,
                  p: 0,
                  minWidth: 0,
                  color: "text.secondary",
                }}
              >
                Clear
              </Button>
            </Box>
          </>
        )}
      </Popover>
    </>
  );
}

// ---------------------------------------------------------------------------
// FilterRow — property picker + operator + value picker
// ---------------------------------------------------------------------------
function FilterRow({
  filter,
  index,
  properties,
  projectId,
  onChange,
  onRemove,
  source = "traces",
  ValuePickerOverride,
  categories,
  freeSoloValues = false,
  operatorFilter,
  defaultOperatorForType,
}) {
  const [pickerAnchor, setPickerAnchor] = useState(null);
  const selectedProp = properties.find((p) => p.id === filter.field);
  const normalizedType = normalizeFieldType(filter.fieldType);
  const isNumber = normalizedType === "number";
  const isDate = normalizedType === "date";
  const isBoolean = normalizedType === "boolean";
  const allOps = getOperatorsForFilter(filter);
  // Optional per-flow allowlist; currentOpDef resolves against the full set.
  const ops = operatorFilter ? allOps.filter(operatorFilter) : allOps;
  const safeOperator = normalizeFilterRowOperator(filter).operator;
  const currentOpDef = allOps.find((o) => o.value === safeOperator);
  const updateRow = useCallback(
    (changes) =>
      onChange(index, {
        ...filter,
        operator: safeOperator,
        ...changes,
      }),
    [filter, index, onChange, safeOperator],
  );
  const rowFreeSoloValues =
    typeof freeSoloValues === "function"
      ? freeSoloValues(filter)
      : freeSoloValues;

  const rowHasInvalidNumeric =
    isNumber &&
    (Array.isArray(filter.value)
      ? filter.value.some((v) => !isValidNumericInput(v))
      : !isValidNumericInput(filter.value));

  const handlePropertySelect = useCallback(
    (prop) => {
      // Preserve custom annotation types (categorical, thumbs, text) —
      // normalizeFieldType would collapse them to "string" losing
      // operator/input specificity.
      const nt =
        prop.type === "categorical" ||
        prop.type === "thumbs" ||
        prop.type === "text" ||
        prop.type === "annotator"
          ? prop.type
          : normalizeFieldType(prop.type);
      // ID-only fields only support "is"; fallback would render blank.
      // defaultOperatorForType: optional per-flow { type: op } override.
      const defaultOp = ID_ONLY_FIELDS.has(prop.id)
        ? "is"
        : defaultOperatorForType?.[nt] || DEFAULT_OP_FOR_TYPE[nt] || "equals";
      let defaultValue;
      if (nt === "number" || nt === "date") defaultValue = "";
      else if (nt === "boolean") defaultValue = "true";
      else if (nt === "text") defaultValue = "";
      else defaultValue = [];
      onChange(index, {
        field: prop.id,
        fieldName: prop.name,
        fieldCategory: prop.category,
        fieldType: nt,
        apiColType: prop.apiColType,
        operator: defaultOp,
        value: defaultValue,
      });
    },
    [index, onChange, defaultOperatorForType],
  );

  const handleOperatorChange = useCallback(
    (e) => {
      const newOp = e.target.value;
      const opList = getOperatorsForFilter(filter);
      const newDef = opList.find((o) => o.value === newOp);
      const oldDef = opList.find((o) => o.value === safeOperator);
      let newVal = filter.value;
      if (isNumber || isDate) {
        if (newDef?.range && !oldDef?.range) newVal = ["", ""];
        else if (!newDef?.range && oldDef?.range) newVal = "";
      }
      if (NO_VALUE_OPS.has(newOp)) newVal = "";
      // Multi → single: drop stale extra picks.
      if (
        SINGLE_VALUE_OPS.has(newOp) &&
        Array.isArray(newVal) &&
        newVal.length > 1
      ) {
        newVal = [newVal[0]];
      }
      // Single → list: picker expects an array.
      if (LIST_VALUE_OPS.has(newOp) && !Array.isArray(newVal)) {
        newVal =
          newVal === "" || newVal === null || newVal === undefined
            ? []
            : [newVal];
      }
      onChange(index, { ...filter, operator: newOp, value: newVal });
    },
    [index, filter, safeOperator, isNumber, isDate, onChange],
  );

  const renderValueInput = () => {
    if (!filter.field) {
      return (
        <Button
          size="small"
          variant="outlined"
          disabled
          sx={{
            flex: 1,
            textTransform: "none",
            fontSize: 12,
            height: 28,
            borderColor: "divider",
          }}
        >
          Select property first
        </Button>
      );
    }

    if (NO_VALUE_OPS.has(safeOperator)) {
      return <Box sx={{ flex: 1 }} />;
    }

    if (isBoolean) {
      return (
        <Select
          size="small"
          value={filter.value ?? "true"}
          onChange={(e) => updateRow({ value: e.target.value })}
          sx={{
            flex: 1,
            minWidth: 80,
            maxWidth: 140,
            fontSize: 12,
            height: 28,
          }}
        >
          <MenuItem value="true" sx={{ fontSize: 12 }}>
            true
          </MenuItem>
          <MenuItem value="false" sx={{ fontSize: 12 }}>
            false
          </MenuItem>
        </Select>
      );
    }

    if (isDate) {
      if (currentOpDef?.range) {
        return (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{
              flex: "1 1 220px",
              minWidth: 0,
              maxWidth: "100%",
              flexWrap: { xs: "wrap", sm: "nowrap" },
            }}
          >
            <TextField
              size="small"
              type="datetime-local"
              value={Array.isArray(filter.value) ? filter.value[0] ?? "" : ""}
              onChange={(e) => {
                const cur = Array.isArray(filter.value)
                  ? [...filter.value]
                  : ["", ""];
                cur[0] = e.target.value;
                updateRow({ value: cur });
              }}
              sx={{ flex: "1 1 120px", minWidth: 0 }}
              inputProps={{
                style: { fontSize: 11, height: 12, padding: "6px 6px" },
              }}
            />
            <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
              and
            </Typography>
            <TextField
              size="small"
              type="datetime-local"
              value={Array.isArray(filter.value) ? filter.value[1] ?? "" : ""}
              onChange={(e) => {
                const cur = Array.isArray(filter.value)
                  ? [...filter.value]
                  : ["", ""];
                cur[1] = e.target.value;
                updateRow({ value: cur });
              }}
              sx={{ flex: "1 1 120px", minWidth: 0 }}
              inputProps={{
                style: { fontSize: 11, height: 12, padding: "6px 6px" },
              }}
            />
          </Stack>
        );
      }
      return (
        <TextField
          size="small"
          type="datetime-local"
          value={typeof filter.value === "string" ? filter.value : ""}
          onChange={(e) => updateRow({ value: e.target.value })}
          sx={{ flex: "1 1 160px", minWidth: 0, maxWidth: "100%" }}
          inputProps={{
            style: { fontSize: 11, height: 12, padding: "6px 6px" },
          }}
        />
      );
    }

    if (isNumber) {
      if (currentOpDef?.range) {
        const minVal = Array.isArray(filter.value) ? filter.value[0] ?? "" : "";
        const maxVal = Array.isArray(filter.value) ? filter.value[1] ?? "" : "";
        const minInvalid = !isValidNumericInput(minVal);
        const maxInvalid = !isValidNumericInput(maxVal);
        return (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{
              flex: "1 1 180px",
              minWidth: 0,
              maxWidth: "100%",
              flexWrap: { xs: "wrap", sm: "nowrap" },
            }}
          >
            <TextField
              size="small"
              type="text"
              inputMode="decimal"
              placeholder="Min"
              value={minVal}
              error={minInvalid}
              helperText={minInvalid ? "Numbers only" : undefined}
              onChange={(e) => {
                const cur = Array.isArray(filter.value)
                  ? [...filter.value]
                  : ["", ""];
                cur[0] = e.target.value.trim();
                updateRow({ value: cur });
              }}
              sx={NUMERIC_TEXTFIELD_SX}
              inputProps={{
                style: { fontSize: 12, height: 12, padding: "6px 8px" },
              }}
              FormHelperTextProps={NUMERIC_HELPER_TEXT_PROPS}
            />
            <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
              and
            </Typography>
            <TextField
              size="small"
              type="text"
              inputMode="decimal"
              placeholder="Max"
              value={maxVal}
              error={maxInvalid}
              helperText={maxInvalid ? "Numbers only" : undefined}
              onChange={(e) => {
                const cur = Array.isArray(filter.value)
                  ? [...filter.value]
                  : ["", ""];
                cur[1] = e.target.value.trim();
                updateRow({ value: cur });
              }}
              sx={NUMERIC_TEXTFIELD_SX}
              inputProps={{
                style: { fontSize: 12, height: 12, padding: "6px 8px" },
              }}
              FormHelperTextProps={NUMERIC_HELPER_TEXT_PROPS}
            />
          </Stack>
        );
      }
      const invalid = !isValidNumericInput(filter.value);
      return (
        <TextField
          size="small"
          type="text"
          inputMode="decimal"
          placeholder="Value"
          value={filter.value ?? ""}
          error={invalid}
          helperText={invalid ? "Numbers only" : undefined}
          onChange={(e) => updateRow({ value: e.target.value.trim() })}
          sx={{
            flex: "1 1 120px",
            minWidth: 0,
            maxWidth: "100%",
            position: "relative",
          }}
          inputProps={{
            style: { fontSize: 12, height: 12, padding: "6px 8px" },
          }}
          FormHelperTextProps={NUMERIC_HELPER_TEXT_PROPS}
        />
      );
    }

    if (usesFreeTextValue(filter.fieldType, source)) {
      return (
        <TextField
          size="small"
          placeholder="Enter text..."
          value={filter.value ?? ""}
          onChange={(e) => updateRow({ value: e.target.value })}
          sx={{ flex: "1 1 160px", minWidth: 0, maxWidth: "100%" }}
          inputProps={{
            style: { fontSize: 12, height: 12, padding: "6px 8px" },
          }}
        />
      );
    }

    const PickerComponent = ValuePickerOverride || ValuePicker;
    return (
      <PickerComponent
        propertyId={filter.field}
        propertyCategory={filter.fieldCategory}
        fieldType={normalizedType}
        projectId={projectId}
        value={filter.value}
        source={source}
        property={properties.find((p) => p.id === filter.field)}
        freeSoloValues={rowFreeSoloValues}
        singleSelect={SINGLE_VALUE_OPS.has(safeOperator)}
        onChange={(newVal) => updateRow({ value: newVal })}
      />
    );
  };

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{
        width: "100%",
        minWidth: 0,
        flexWrap: "wrap",
        mb: rowHasInvalidNumeric ? 1.5 : 0,
      }}
    >
      <CustomTooltip
        show={!!selectedProp?.name}
        arrow
        size="small"
        type="black"
        title={selectedProp?.name || ""}
      >
        <Button
          ref={(el) => el}
          size="small"
          variant="outlined"
          onClick={(e) => setPickerAnchor(e.currentTarget)}
          endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
          sx={{
            textTransform: "none",
            fontSize: 12,
            height: 28,
            flex: "1 1 150px",
            minWidth: 0,
            maxWidth: { xs: "100%", sm: 180 },
            borderColor: "divider",
            color: filter.field ? "text.primary" : "text.disabled",
            justifyContent: "space-between",
          }}
        >
          <Typography
            noWrap
            sx={{
              fontSize: 12,
              maxWidth: "100%",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {selectedProp?.name || "Property"}
          </Typography>
        </Button>
      </CustomTooltip>
      <PropertyPicker
        anchorEl={pickerAnchor}
        open={Boolean(pickerAnchor)}
        onClose={() => setPickerAnchor(null)}
        properties={properties}
        categories={categories}
        onSelect={handlePropertySelect}
      />

      <Select
        size="small"
        value={safeOperator}
        onChange={handleOperatorChange}
        sx={{
          flex: "0 1 128px",
          minWidth: 90,
          maxWidth: { xs: "100%", sm: 150 },
          fontSize: 12,
          height: 28,
        }}
      >
        {ops.map((op) => (
          <MenuItem key={op.value} value={op.value} sx={{ fontSize: 12 }}>
            {op.label}
          </MenuItem>
        ))}
      </Select>

      {renderValueInput()}

      <IconButton
        size="small"
        onClick={() => onRemove(index)}
        sx={{ p: 0.25, flexShrink: 0, ml: "auto" }}
      >
        <Iconify icon="mdi:close" width={14} />
      </IconButton>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// TraceFilterPanel
// ---------------------------------------------------------------------------
const DEFAULT_ROW = {
  field: "",
  fieldCategory: "system",
  operator: "in",
  value: [],
};

const TraceFilterPanel = ({
  anchorEl,
  open,
  onClose,
  currentFilters,
  onApply,
  filterFields,
  source = "traces",
  tab = null,
  projectId: projectIdProp,
  properties: propertiesOverride,
  ValuePickerOverride,
  showAi = true,
  showQueryTab = true,
  categories: categoriesOverride,
  propertyFilter,
  operatorFilter,
  defaultOperatorForType,
  panelWidth,
  defaultRow: defaultRowOverride,
  isSimulator = false,
  freeSoloValues = false,
  isSpansView = false,
}) => {
  const { observeId: routeObserveId } = useParams();
  const observeId = projectIdProp || routeObserveId;
  const skipDynamicProperties = Boolean(propertiesOverride);
  const dynamicPropertySource = isSpansView ? "spans" : "traces";
  const { data: dynamicProperties = [], isLoading: dynamicPropsLoading } =
    useTraceFilterProperties(observeId, {
      enabled: !skipDynamicProperties,
      isSimulator,
      sourceScope: dynamicPropertySource,
    });
  // Merge: static trace fields + dynamic dashboard properties + any extra static fields
  const properties = useMemo(() => {
    if (propertiesOverride) {
      return propertyFilter
        ? propertiesOverride.filter(propertyFilter)
        : propertiesOverride;
    }
    // Start with static trace fields (trace_name, status, model, etc.) —
    // prepend trace_id / span_id when rendered inside the LLM Tracing
    // trace or span tab. In spans view, relabel "Trace Name" to "Span Name".
    const staticProps = getTraceFilterFields(tab).map((f) =>
      toStaticFilterProperty(f, isSpansView),
    );
    const knownIds = new Set(staticProps.map((p) => p.id));
    // Add dynamic properties not already covered by static fields
    const dynamicExtras = dynamicProperties.filter((p) => !knownIds.has(p.id));
    // Add any extra filterFields not already covered
    const allIds = new Set([...knownIds, ...dynamicExtras.map((p) => p.id)]);
    const fieldExtras = (filterFields || [])
      .filter((f) => !allIds.has(f.id))
      .map((f) => ({
        id: f.id || f.value,
        name: f.name || f.label,
        category: "system",
        type: f.type || "string",
      }));
    const merged = [...staticProps, ...dynamicExtras, ...fieldExtras];
    return propertyFilter ? merged.filter(propertyFilter) : merged;
  }, [
    dynamicProperties,
    filterFields,
    propertiesOverride,
    propertyFilter,
    tab,
    isSpansView,
  ]);
  const propertyById = useMemo(
    () => Object.fromEntries(properties.map((p) => [p.id, p])),
    [properties],
  );
  const propsLoading = skipDynamicProperties ? false : dynamicPropsLoading;
  const effectiveCategories = categoriesOverride ?? CATEGORIES;
  const effectiveDefaultRow = defaultRowOverride || DEFAULT_ROW;
  const [activeTab, setActiveTab] = useState("basic");
  const [aiQuery, setAiQuery] = useState("");
  // True when the last AI query returned zero filters (shows inline hint).
  const [aiEmpty, setAiEmpty] = useState(false);
  // AI filter schema: exclude `attribute` category — those are typically
  // 100s–1000s of free-form keys that aren't referenced by name in natural
  // language and only slow step-1 field selection down without helping.
  const aiFilterSchema = useMemo(
    () =>
      properties
        .filter((p) => p.category !== "attribute")
        .map((p) => ({
          field: p.id,
          label: p.name,
          category: p.category,
          type: p.type || "string",
          operators: getOperators(p.type).map((o) => o.value),
        })),
    [properties],
  );
  const {
    parseQuery: aiParseQuery,
    loading: aiLoading,
    error: aiError,
  } = useAIFilter(aiFilterSchema);
  const [rows, setRows] = useState([{ ...DEFAULT_ROW }]);
  // Serialized snapshot of the filter set last sent to onApply. Auto-apply
  // compares against this so we only hit the API when the applyable filter set
  // actually changes — picking a field/operator with no value, or re-opening
  // the popover, yields the same set and is skipped.
  const lastAppliedRef = useRef(undefined);

  // Convert dashboard properties to QueryInput format (same IDs as dashboard API)
  const queryFilterFields = useMemo(() => {
    return properties.map((p) => ({
      value: p.id,
      label: p.name,
      type: p.type || "string",
      choices: p.choices,
      panelType: p.type || "string",
      category: p.category, // system, eval, annotation, attribute
      apiColType: p.apiColType,
    }));
  }, [properties]);
  const queryFieldMap = useMemo(
    () => Object.fromEntries(queryFilterFields.map((f) => [f.value, f])),
    [queryFilterFields],
  );

  // Query tab — fetch values for the selected field
  const [queryField, setQueryField] = useState(null);
  const queryFieldProp = properties.find((p) => p.id === queryField);
  const queryMetricType = (() => {
    const cat = queryFieldProp?.category || "system";
    if (cat === "system") return "system_metric";
    if (cat === "eval") return "eval_metric";
    if (cat === "annotation") return "annotation_metric";
    if (cat === "attribute") return "custom_attribute";
    return "system_metric";
  })();
  const { data: queryValueOptions = [], isLoading: queryValuesLoading } =
    useDashboardFilterValues({
      metricName: queryField || "",
      metricType: queryMetricType,
      projectIds: observeId ? [observeId] : [],
      source,
    });

  useEffect(() => {
    if (open) {
      if (currentFilters?.length) {
        // Enrich rows with fieldCategory and fieldType from properties lookup
        const enriched = currentFilters.map((f) => {
          const prop = propertyById[f.field];
          const fieldType = f.fieldType || prop?.type || "string";
          // ID-only fields (trace_id / span_id) bypass the string-op
          // rewrite — ID_ONLY_OPS = [{ value: "is" }] so anything other
          // than "is" renders blank in the operator Select.
          const hydratedOp = ID_ONLY_FIELDS.has(f.field)
            ? "is"
            : (fieldType === "string" || fieldType === "text") &&
                HYDRATE_STRING_OP[f.operator]
              ? HYDRATE_STRING_OP[f.operator]
              : f.operator;
          // Scalar legacy `equals` value → array for the multi-select picker.
          let value = f.value;
          if (
            hydratedOp !== f.operator &&
            LIST_VALUE_OPS.has(hydratedOp) &&
            !Array.isArray(value)
          ) {
            value =
              value === "" || value === null || value === undefined
                ? []
                : [value];
          }
          return normalizeFilterRowOperator({
            ...f,
            fieldCategory: resolveFieldCategory(f.fieldCategory, prop),
            fieldName: f.fieldName || prop?.name,
            fieldType,
            apiColType: f.apiColType || prop?.apiColType,
            operator: hydratedOp,
            value,
          });
        });
        setRows(enriched);
        // Seed last-applied with the already-applied set so the first
        // auto-apply pass after opening doesn't refire the same filters.
        lastAppliedRef.current = serializeFilterSet(
          computeValidFilters(enriched),
        );
      } else {
        const initialRows = [{ ...effectiveDefaultRow }];
        setRows(initialRows);
        lastAppliedRef.current = serializeFilterSet(
          computeValidFilters(initialRows),
        );
      }
    }
    // Initialize rows only when the popover OPENS. With auto-apply, picking a
    // value pushes to currentFilters; if this effect also re-ran on
    // currentFilters it would reset rows and "unchoose" the value (feedback
    // loop). While open, local rows are the source of truth.
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const queryInputRef = useRef(null);

  // Pure converter: query-input tokens → FilterRow shape. Extracted so the
  // query-apply path can run it directly against tokens that were just
  // flushed from QueryInput, without waiting for the setRows state cycle.
  const queryTokensToRows = useCallback(
    (tokens) =>
      tokens.map((t) => {
        const queryFieldDef = queryFieldMap[t.field];
        const prop = propertyById[t.field];
        return {
          field: t.field,
          fieldName: prop?.name || queryFieldDef?.label,
          fieldCategory: resolveFieldCategory(undefined, prop || queryFieldDef),
          fieldType:
            prop?.type ||
            queryFieldDef?.panelType ||
            (queryFieldDef?.type === "enum" ? "categorical" : "string"),
          apiColType: prop?.apiColType || queryFieldDef?.apiColType,
          operator: QUERY_TO_BASIC_OP[t.operator] || t.operator,
          value: NO_VALUE_OPS.has(t.operator)
            ? ""
            : Array.isArray(t.value)
              ? t.value
              : [t.value],
        };
      }),
    [propertyById, queryFieldMap],
  );

  const handleQueryTokensChange = useCallback(
    (tokens) => {
      const converted = queryTokensToRows(tokens);
      setRows(converted.length ? converted : [{ ...effectiveDefaultRow }]);
    },
    [effectiveDefaultRow, queryTokensToRows],
  );

  const queryGetOperators = useCallback(
    (type, field) => {
      const ops = getOperatorsForFilter({ field, fieldType: type });
      const allowed = operatorFilter ? ops.filter(operatorFilter) : ops;
      return allowed.map((op) =>
        NO_VALUE_OPS.has(op.value) ? { ...op, noValue: true } : op,
      );
    },
    [operatorFilter],
  );

  const handleChange = useCallback((idx, updated) => {
    setRows((prev) => prev.map((r, i) => (i === idx ? updated : r)));
  }, []);

  const handleRemove = useCallback(
    (idx) => {
      setRows((prev) => {
        const next = prev.filter((_, i) => i !== idx);
        return next.length ? next : [{ ...effectiveDefaultRow }];
      });
    },
    [effectiveDefaultRow],
  );

  // Auto-apply: filters take effect as soon as a value is chosen (debounced),
  // so there's no Apply button — only Clear all. We apply WITHOUT closing the
  // popover so the user can keep adjusting filters and see them apply live.
  const autoApplyTimerRef = useRef(null);
  // Apply only when the resulting filter set differs from the last one sent.
  const applyIfChanged = useCallback(
    (sourceRows) => {
      // Hold while a numeric row is mid-edit so partial/invalid values don't
      // auto-fire and a half-filled range doesn't drop the applied filter.
      if (hasIncompleteNumericRow(sourceRows)) return;
      const next = computeValidFilters(sourceRows);
      const serialized = serializeFilterSet(next);
      if (serialized === lastAppliedRef.current) return;
      lastAppliedRef.current = serialized;
      onApply(next);
    },
    [onApply],
  );

  useEffect(() => {
    if (!open) return undefined;
    if (autoApplyTimerRef.current) clearTimeout(autoApplyTimerRef.current);
    autoApplyTimerRef.current = setTimeout(() => applyIfChanged(rows), 350);
    return () => {
      if (autoApplyTimerRef.current) clearTimeout(autoApplyTimerRef.current);
    };
  }, [rows, open, applyIfChanged]); // eslint-disable-line react-hooks/exhaustive-deps

  // Flush pending apply on close; bypass ref lets programmatic applies skip it.
  const wasOpenRef = useRef(open);
  const bypassNextCloseFlushRef = useRef(false);
  useEffect(() => {
    if (wasOpenRef.current && !open) {
      if (bypassNextCloseFlushRef.current) {
        bypassNextCloseFlushRef.current = false;
        wasOpenRef.current = open;
        return;
      }
      if (autoApplyTimerRef.current) {
        clearTimeout(autoApplyTimerRef.current);
        autoApplyTimerRef.current = null;
      }
      const flushed = queryInputRef.current?.flushPartial?.();
      if (flushed && flushed.length) {
        const flushedRows = queryTokensToRows(flushed);
        setRows(flushedRows);
        applyIfChanged(flushedRows);
      } else {
        applyIfChanged(rows);
      }
    }
    wasOpenRef.current = open;
  }, [open, rows, applyIfChanged, queryTokensToRows]);

  const handleClear = useCallback(() => {
    setRows([{ ...effectiveDefaultRow }]);
    lastAppliedRef.current = serializeFilterSet(null);
    onApply(null);
    onClose();
  }, [onApply, onClose, effectiveDefaultRow]);

  const handleAiFilter = useCallback(async () => {
    if (!aiQuery.trim()) return;
    setAiEmpty(false);
    const aiFilters = await aiParseQuery(aiQuery, {
      smart: true,
      projectId: observeId,
      source,
    });
    if (aiFilters.length > 0) {
      const aiRows = aiFilters.map((f) => {
        const prop = properties.find((p) => p.id === f.field);
        const fieldType = prop?.type || "string";
        return {
          field: f.field,
          fieldCategory: resolveFieldCategory(undefined, prop),
          fieldType,
          apiColType: prop?.apiColType,
          operator: f.operator || DEFAULT_OP_FOR_TYPE[fieldType] || "equals",
          value: Array.isArray(f.value) ? f.value : [f.value],
        };
      });
      // Additive: append AI rows to existing valid filters, no dedup.
      const merged = [...(computeValidFilters(rows) || []), ...aiRows];
      const validFilters = computeValidFilters(merged);
      setRows(merged);
      lastAppliedRef.current = serializeFilterSet(validFilters);
      onApply(validFilters);
      setAiQuery("");
      bypassNextCloseFlushRef.current = true;
      onClose();
    } else {
      setAiEmpty(true);
    }
  }, [
    aiQuery,
    aiParseQuery,
    observeId,
    source,
    properties,
    rows,
    onApply,
    onClose,
  ]);

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{
        paper: {
          sx: {
            width: { xs: "calc(100vw - 24px)", sm: panelWidth || 560 },
            maxWidth: "calc(100vw - 24px)",
            borderRadius: "10px",
            mt: 0.5,
            p: 1,
            overflowX: "hidden",
          },
        },
      }}
    >
      <Stack spacing={0}>
        {/* AI input */}
        {showAi && (
          <>
            <TextField
              size="small"
              fullWidth
              placeholder={
                aiLoading
                  ? "Parsing with AI..."
                  : "Ask AI — e.g. 'show traces with errors on gpt-4'"
              }
              value={aiQuery}
              onChange={(e) => {
                setAiQuery(e.target.value);
                setAiEmpty(false);
              }}
              disabled={aiLoading}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAiFilter();
              }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon={aiLoading ? "mdi:loading" : "mdi:creation"}
                      width={16}
                      sx={{
                        color: "primary.main",
                        ...(aiLoading
                          ? {
                              animation: "spin 1s linear infinite",
                              "@keyframes spin": {
                                from: { transform: "rotate(0deg)" },
                                to: { transform: "rotate(360deg)" },
                              },
                            }
                          : {}),
                      }}
                    />
                  </InputAdornment>
                ),
                endAdornment:
                  aiQuery.trim() && !aiLoading ? (
                    <InputAdornment position="end">
                      <IconButton
                        size="small"
                        onClick={handleAiFilter}
                        sx={{ p: 0.25 }}
                      >
                        <Iconify icon="mdi:arrow-right" width={16} />
                      </IconButton>
                    </InputAdornment>
                  ) : null,
                sx: { fontSize: 13, height: 32 },
              }}
            />
            {aiError && (
              <Typography
                variant="caption"
                sx={{ fontSize: 11, color: "text.secondary", px: 0.5 }}
              >
                AI unavailable, use filters below
              </Typography>
            )}
            {aiEmpty && !aiError && !aiLoading && (
              <Typography
                variant="caption"
                sx={{ fontSize: 11, color: "text.secondary", px: 0.5 }}
              >
                Could not derive filters from that query. Try rephrasing or add
                a filter manually below.
              </Typography>
            )}
          </>
        )}

        {/* Tabs */}
        {showQueryTab && (
          <Tabs
            value={activeTab}
            onChange={(_, v) => setActiveTab(v)}
            sx={{
              minHeight: 24,
              borderBottom: "1px solid",
              borderColor: "divider",
              "& .MuiTab-root": {
                minHeight: 24,
                py: 0.25,
                px: 1,
                textTransform: "none",
                fontSize: 13,
                fontWeight: 500,
                minWidth: 0,
              },
            }}
          >
            <Tab value="basic" label="Basic" />
            <Tab value="query" label="Query" />
          </Tabs>
        )}

        {/* Basic tab */}
        {(activeTab === "basic" || !showQueryTab) && (
          <Box sx={{ px: 0.5, pt: 0.25 }}>
            {propsLoading ? (
              <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
                <CircularProgress size={20} />
              </Box>
            ) : (
              <Stack spacing={1}>
                {rows.map((row, idx) => (
                  <FilterRow
                    key={idx}
                    filter={row}
                    index={idx}
                    properties={properties}
                    projectId={observeId}
                    onChange={handleChange}
                    onRemove={handleRemove}
                    source={source}
                    ValuePickerOverride={ValuePickerOverride}
                    categories={effectiveCategories}
                    freeSoloValues={freeSoloValues}
                    operatorFilter={operatorFilter}
                    defaultOperatorForType={defaultOperatorForType}
                  />
                ))}
              </Stack>
            )}
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mt: 1.5, gap: 1, flexWrap: "wrap" }}
            >
              <Button
                size="small"
                startIcon={<Iconify icon="mdi:plus" width={14} />}
                onClick={() =>
                  setRows((prev) => [...prev, { ...effectiveDefaultRow }])
                }
                sx={{
                  textTransform: "none",
                  fontSize: 12,
                  color: "text.secondary",
                }}
              >
                Add filter
              </Button>
              <Stack direction="row" spacing={1} sx={{ ml: "auto" }}>
                <Button
                  size="small"
                  data-filter-panel-action="clear"
                  onClick={handleClear}
                  sx={{ textTransform: "none", fontSize: 12 }}
                >
                  Clear all
                </Button>
              </Stack>
            </Stack>
          </Box>
        )}

        {/* Query tab — inline token builder using same properties from dashboard API */}
        {showQueryTab && activeTab === "query" && (
          <Box sx={{ px: 0.5, pt: 0.25 }}>
            <QueryInput
              ref={queryInputRef}
              filterFields={queryFilterFields}
              fieldMap={queryFieldMap}
              getOperators={queryGetOperators}
              onApply={handleQueryTokensChange}
              initialTokens={rows
                .filter((r) => {
                  if (!r.field) return false;
                  if (NO_VALUE_OPS.has(normalizeFilterRowOperator(r).operator))
                    return true;
                  return Array.isArray(r.value)
                    ? r.value.length > 0
                    : r.value !== "" &&
                        r.value !== undefined &&
                        r.value !== null;
                })
                .map((r) => ({
                  field: r.field,
                  operator:
                    BASIC_TO_QUERY_OP[normalizeFilterRowOperator(r).operator] ||
                    normalizeFilterRowOperator(r).operator,
                  value: Array.isArray(r.value) ? r.value : r.value || "",
                }))}
              valueOptions={queryValueOptions}
              valueLoading={queryValuesLoading}
              onFieldChange={setQueryField}
            />
            <Stack
              direction="row"
              justifyContent="flex-end"
              spacing={1}
              sx={{ mt: 1 }}
            >
              <Button
                size="small"
                data-filter-panel-action="clear"
                onClick={handleClear}
                sx={{ textTransform: "none", fontSize: 12 }}
              >
                Clear all
              </Button>
            </Stack>
            <Typography
              sx={{ fontSize: 11, color: "text.disabled", mt: 1, px: 0.5 }}
            >
              Type property → pick operator → pick/type value. Backspace to
              undo. Click chip to edit.
            </Typography>
          </Box>
        )}
      </Stack>
    </Popover>
  );
};

TraceFilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  currentFilters: PropTypes.array,
  onApply: PropTypes.func.isRequired,
  filterFields: PropTypes.array,
  source: PropTypes.string,
  tab: PropTypes.oneOf(["trace", "spans"]),
  projectId: PropTypes.string,
  properties: PropTypes.array,
  ValuePickerOverride: PropTypes.elementType,
  showAi: PropTypes.bool,
  showQueryTab: PropTypes.bool,
  categories: PropTypes.array,
  propertyFilter: PropTypes.func,
  operatorFilter: PropTypes.func,
  defaultOperatorForType: PropTypes.object,
  panelWidth: PropTypes.number,
  defaultRow: PropTypes.object,
  isSimulator: PropTypes.bool,
  freeSoloValues: PropTypes.oneOfType([PropTypes.bool, PropTypes.func]),
  isSpansView: PropTypes.bool,
};

export default React.memo(TraceFilterPanel);
