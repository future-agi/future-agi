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
import { useInfiniteQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import BoundedCursorPaginationControl from "src/components/BoundedCursorPaginationControl";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import axios, { endpoints } from "src/utils/axios";
import { SpanTypes } from "src/utils/constant";
import {
  buildPropertyRegistryId,
  isPropertyCatalogNotReadyError,
  PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
  useDashboardFilterValues,
  useLegacyDashboardMetricsPaginated,
  usePropertyCatalog,
} from "src/hooks/useDashboards";
import { useDebounce } from "src/hooks/use-debounce";
import { useAIFilter } from "src/hooks/use-ai-filter";
import { FILTER_INPUT_TYPES } from "src/utils/constants";
import { QueryInput } from "src/components/filter-panel";
import {
  FILTER_STRING_MAX_UTF8_BYTES,
  getUtf8ByteLength,
  TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES,
} from "src/api/contracts/filter-contract";
import {
  getPickerOptionExactMatches,
  getPickerOptionLabel,
  getPickerOptionSearchText,
  getPickerOptionSecondaryLabel,
  getPickerOptionType,
  getPickerOptionValue,
  getPickerValueIdentity,
  normalizePickerValues,
  usesFreeTextValue,
} from "./filterValuePickerUtils";
import { ID_ONLY_FIELDS } from "./idFields";
import {
  getAttributeLookupMessage,
  getFilterValueReadMessage,
  getQueryReadMessage,
} from "src/utils/queryReadState";
import { useExactTraceAttributeProperties } from "./useExactTraceAttributeProperties";
import {
  normalizeVoiceCallStatus,
  VOICE_CALL_FILTER_FIELDS,
} from "./voiceCallFilterFields";
import {
  FILTER_AUTO_APPLY_DEBOUNCE_MS,
  FILTER_VALUE_PAGE_SIZE,
  FILTER_VALUE_SEARCH_DEBOUNCE_MS,
  PROPERTY_CATALOG_LEGACY_CACHE_TIME_MS,
  PROPERTY_CATALOG_LEGACY_PAGE_SIZE,
  PROPERTY_CATALOG_LEGACY_STALE_TIME_MS,
  PROPERTY_PICKER_PREFETCH_MARGIN_PX,
  PROPERTY_PICKER_RENDER_BATCH_SIZE,
  PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS,
  PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
} from "src/config/runtime_limits";

function useSingleFlightPageRequest({ identity, enabled, request }) {
  const activeRequestRef = useRef(null);

  return useCallback(() => {
    const activeRequest = activeRequestRef.current;
    if (activeRequest?.identity === identity) return activeRequest.promise;
    if (!enabled) return undefined;

    const response = request();
    // React Query returns a promise. Keep synchronous test/legacy callbacks
    // compatible without leaving a phantom request latched until a microtask.
    if (!response || typeof response.then !== "function") return response;
    const promise = Promise.resolve(response);
    const nextRequest = { identity, promise };
    activeRequestRef.current = nextRequest;
    const clearRequest = () => {
      if (activeRequestRef.current === nextRequest) {
        activeRequestRef.current = null;
      }
    };
    promise.then(clearRequest, clearRequest);
    return promise;
  }, [enabled, identity, request]);
}

const filterValueAdapterMetricName = (source, propertyId) =>
  source === "sessions" && propertyId === "session_id" ? "session" : propertyId;

const filterValueTransportSource = (source, metricType) =>
  source === "sessions" && metricType === "custom_attribute"
    ? "traces"
    : source;

const defaultPropertyNamespace = (tab, source) => {
  if (tab === "voiceCalls") return "voice_calls";
  if (source === "dataset") return "dataset_column";
  return source;
};

const categoryForFilterField = (field) => {
  if (field?.category) return field.category;
  if (field?.apiColType === "EVAL_METRIC") return "eval";
  if (field?.apiColType === "ANNOTATION") return "annotation";
  if (field?.apiColType === "SPAN_ATTRIBUTE") return "attribute";
  if (field?.apiColType === "CUSTOM_COLUMN") return "dataset";
  return "system";
};

const apiColTypeForFilterField = (field, category) => {
  if (field?.apiColType) return field.apiColType;
  if (["eval", "eval_metric", "evalMetric"].includes(category)) {
    return "EVAL_METRIC";
  }
  if (
    ["annotation", "annotation_metric", "annotationMetric"].includes(category)
  ) {
    return "ANNOTATION";
  }
  if (["attribute", "custom_attribute", "customAttribute"].includes(category)) {
    return "SPAN_ATTRIBUTE";
  }
  if (["dataset", "custom_column", "customColumn"].includes(category)) {
    return "CUSTOM_COLUMN";
  }
  return "SYSTEM_METRIC";
};

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
    choices: SpanTypes.map((s) => s.value),
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
  if (tab === "voiceCalls") return VOICE_CALL_FILTER_FIELDS;
  if (tab === "trace") return [TRACE_ID_FIELD, ...BASE_TRACE_FILTER_FIELDS];
  if (tab === "spans")
    return [TRACE_ID_FIELD, SPAN_ID_FIELD, ...BASE_TRACE_FILTER_FIELDS];
  return BASE_TRACE_FILTER_FIELDS;
};

// Map a static trace field to a picker property. In spans view the root
// "Trace Name" field is reused as "Span Name" — remap its id so the picker
// fires a distinct span_name request instead of duplicating the name request.
export const toStaticFilterProperty = (
  field,
  isSpansView = false,
  propertyNamespace = "traces",
  source = "traces",
) => {
  const isSpanName = isSpansView && field.value === "name";
  const id = isSpanName ? "span_name" : field.value;
  const category = categoryForFilterField(field);
  const property = {
    id,
    name: isSpanName ? "Span Name" : field.label,
    category,
    // Unified-catalog search cannot index definitions that exist only in the
    // frontend registry. Keep those exact definitions eligible for the
    // narrowly-scoped search fallback below.
    catalogSearchFallback: true,
    // Pinned so the eval-task wire encoding doesn't have to guess
    // from `category` alone — without this every static field would
    // round-trip through the chain with apiColType=undefined and
    // get coerced to SPAN_ATTRIBUTE downstream.
    apiColType: apiColTypeForFilterField(field, category),
    type: field.type === "enum" ? "string" : field.type,
    ...(field.choices ? { choices: field.choices } : {}),
    ...(field.responseKey ? { responseKey: field.responseKey } : {}),
    ...(field.searchAliases ? { searchAliases: field.searchAliases } : {}),
    ...(field.dynamicAliases ? { dynamicAliases: field.dynamicAliases } : {}),
    ...(field.legacyWireValues
      ? { legacyWireValues: field.legacyWireValues }
      : {}),
    ...(field.allowCustomValue !== undefined
      ? { allowCustomValue: field.allowCustomValue }
      : {}),
  };
  return attachPropertyRegistryIdentity(property, source, propertyNamespace, {
    suppliedRegistryId: field.registryId || field.property_id,
  });
};

// Static fields are authoritative for their own ids. Voice fields also own a
// small set of legacy/system aliases published by the dashboard catalog. Only
// suppress those aliases in the System category: a raw span attribute with the
// same spelling remains a distinct, selectable field under Attributes.
export function mergeTraceFilterProperties({
  tab,
  isSpansView = false,
  source = "traces",
  propertyNamespace,
  dynamicProperties = [],
  filterFields = [],
}) {
  const effectivePropertyNamespace =
    propertyNamespace || defaultPropertyNamespace(tab, source);
  const staticProps = getTraceFilterFields(tab).map((field) =>
    toStaticFilterProperty(
      field,
      isSpansView,
      effectivePropertyNamespace,
      source,
    ),
  );
  const canonicalIds = new Set(staticProps.map((property) => property.id));
  const coveredSystemAliases = new Set(
    staticProps.flatMap((property) => [
      property.responseKey,
      ...(property.legacyWireValues || []),
      ...(property.dynamicAliases || []),
    ]),
  );
  const dynamicExtras = dynamicProperties
    .filter((property) => {
      const isSystemProperty =
        property.category === "system" ||
        property.apiColType === "SYSTEM_METRIC";
      return !(
        isSystemProperty &&
        (canonicalIds.has(property.id) || coveredSystemAliases.has(property.id))
      );
    })
    .map((property) =>
      attachPropertyRegistryIdentity(
        property,
        source,
        effectivePropertyNamespace,
      ),
    );
  const dynamicSystemIds = dynamicExtras
    .filter(
      (property) =>
        property.category === "system" ||
        property.apiColType === "SYSTEM_METRIC",
    )
    .map((property) => property.id);
  const allIds = new Set([
    ...canonicalIds,
    ...coveredSystemAliases,
    ...dynamicSystemIds,
  ]);
  const fieldExtras = filterFields
    .filter((field) => !allIds.has(field.id || field.value))
    .map((field) => {
      const id = field.id || field.value;
      const category = categoryForFilterField(field);
      const property = {
        id,
        name: field.name || field.label,
        category,
        catalogSearchFallback: true,
        rawCategory: field.rawCategory,
        apiColType: apiColTypeForFilterField(field, category),
        type: field.type || "string",
        ...(field.choices ? { choices: field.choices } : {}),
        ...(field.allowCustomValue !== undefined
          ? { allowCustomValue: field.allowCustomValue }
          : {}),
      };
      return attachPropertyRegistryIdentity(
        property,
        source,
        effectivePropertyNamespace,
        { suppliedRegistryId: field.registryId || field.property_id },
      );
    });
  return [...staticProps, ...dynamicExtras, ...fieldExtras];
}

const propertyMetricType = (property, source) => {
  const apiColType = property?.apiColType;
  const category = property?.category || property?.rawCategory;
  if (
    apiColType === "EVAL_METRIC" ||
    ["eval", "eval_metric", "evalMetric"].includes(category)
  ) {
    return "eval_metric";
  }
  if (
    apiColType === "ANNOTATION" ||
    ["annotation", "annotation_metric", "annotationMetric"].includes(category)
  ) {
    return "annotation_metric";
  }
  if (
    apiColType === "CUSTOM_COLUMN" ||
    ["custom_column", "customColumn"].includes(category) ||
    source === "dataset_column"
  ) {
    return "custom_column";
  }
  if (
    apiColType === "SPAN_ATTRIBUTE" ||
    ["attribute", "custom_attribute", "customAttribute"].includes(category)
  ) {
    return "custom_attribute";
  }
  return "system_metric";
};

function attachPropertyRegistryIdentity(
  property,
  source,
  propertyNamespace = source,
  { suppliedRegistryId } = {},
) {
  if (!property) return property;
  const registryId =
    suppliedRegistryId || property.registryId || property.property_id;
  if (registryId) {
    return property.registryId === registryId
      ? property
      : { ...property, registryId };
  }
  const metricType = propertyMetricType(property, source);
  const metricName = filterValueAdapterMetricName(
    source,
    property.id || property.value || "",
  );
  return {
    ...property,
    registryId: buildPropertyRegistryId({
      metricName,
      metricType,
      source: propertyNamespace,
    }),
  };
}

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

const MAP_OPS = [
  { value: "equals", label: "equals" },
  { value: "not_equals", label: "not equals" },
  { value: "contains", label: "contains entries" },
  { value: "not_contains", label: "does not contain entries" },
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
const MAP_TYPES = new Set(["map", "object"]);

const normalizeFieldType = (rawType) => {
  if (!rawType) return "string";
  const t = String(rawType).toLowerCase();
  if (NUMERIC_TYPES.has(t)) return "number";
  if (DATE_TYPES.has(t)) return "date";
  if (BOOLEAN_TYPES.has(t)) return "boolean";
  if (ARRAY_TYPES.has(t)) return "array";
  if (MAP_TYPES.has(t)) return "map";
  return "string";
};

const isPlainObject = (value) =>
  value !== null &&
  typeof value === "object" &&
  !Array.isArray(value) &&
  (Object.getPrototypeOf(value) === Object.prototype ||
    Object.getPrototypeOf(value) === null);

// Map predicates deliberately accept only the same finite shape as the API:
// one non-empty, flat JSON object whose values are non-null scalar values.
// Returning null instead of throwing lets the editor hold partial JSON without
// firing a broken auto-apply request while the user is still typing.
export const parseMapFilterValue = (rawValue) => {
  let value = rawValue;
  if (typeof rawValue === "string") {
    const trimmed = rawValue.trim();
    if (!trimmed) return null;
    try {
      value = JSON.parse(trimmed);
    } catch {
      return null;
    }
  }
  if (!isPlainObject(value) || Object.keys(value).length === 0) return null;
  const entries = Object.entries(value);
  if (
    entries.some(
      ([key, member]) =>
        !key ||
        member === null ||
        member === undefined ||
        (typeof member === "object" && member !== null) ||
        (typeof member === "number" && !Number.isFinite(member)) ||
        !["string", "number", "boolean"].includes(typeof member),
    )
  ) {
    return null;
  }
  return Object.fromEntries(
    entries.sort(([left], [right]) => left.localeCompare(right)),
  );
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
  if (t === "map") return MAP_OPS;
  return STRING_OPS;
};

// Wrapper that special-cases ID-only fields. Use from FilterRow + apply
// validation; keep `getOperators` as the pure type → ops mapping (Query
// tab + AI filter schema rely on the type-only behavior).
const getOperatorsForFilter = (filter, property) => {
  if (filter?.field && ID_ONLY_FIELDS.has(filter.field)) return ID_ONLY_OPS;
  const ops = getOperators(filter?.fieldType);
  // A property may narrow its own operators — e.g. span type, where the API
  // takes a value list and has nowhere to put an operator, so anything but
  // "is one of" would be a no-op or an inversion.
  if (!Array.isArray(property?.operators)) return ops;
  // A property whose declared operators share nothing with its type would
  // narrow the row down to an empty dropdown, so fall back to the type's own.
  const narrowed = ops.filter((op) => property.operators.includes(op.value));
  return narrowed.length ? narrowed : ops;
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
  map: "contains",
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
  const valid = rows
    .map(normalizeFilterRowOperator)
    .map((row) => {
      if (
        normalizeFieldType(row.fieldType) === "map" &&
        !NO_VALUE_OPS.has(row.operator)
      ) {
        const value = parseMapFilterValue(row.value);
        return value ? { ...row, value } : null;
      }
      return row;
    })
    .filter((r) => {
      if (!r?.field) return false;
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
      registryId: f.registryId,
      apiColType: f.apiColType,
      operator: normalizeFilterRowOperator(f).operator,
      value: f.value,
      valueTypes: f.valueTypes,
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

export const hasIncompleteMapRow = (rows) =>
  rows.some((row) => {
    if (normalizeFieldType(row.fieldType) !== "map") return false;
    if (NO_VALUE_OPS.has(row.operator)) return false;
    if (row.value === "" || row.value === undefined || row.value === null)
      return false;
    return parseMapFilterValue(row.value) === null;
  });

// Scalar ops — value picker forces single-select. Multi-value goes via in/not_in.
const USER_SELECTABLE_TYPES = new Set(["text", "number", "boolean"]);

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
// Keep the initial DOM bounded, then reveal already-fetched properties in
// deliberate batches. This is a render batch, not a result ceiling: every
// retained key remains reachable without forcing thousands of menu rows into
// the first paint.
const normalizePropertySearchText = (value) =>
  String(value || "")
    .toLowerCase()
    .replace(/[_\-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

// Canonical field identity is deliberately stricter than fuzzy picker search.
// Attribute ids are case- and punctuation-sensitive backend keys: `trace_id`
// and `trace.id` must never become the same identity merely because both are
// convenient fuzzy matches. System fields may additionally match their exact
// display label (for example `call_id` <-> `Call ID`), while punctuation is
// still preserved so a raw attribute lookup cannot be shadowed by `Trace ID`.
const normalizeCanonicalPropertyIdentity = (value) =>
  String(value || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();

const propertyMatchesRawId = (property, rawQuery) =>
  Boolean(rawQuery) && String(property?.id || "").trim() === rawQuery;

const propertyMatchesCanonicalSystemIdentity = (property, rawQuery) => {
  if (!rawQuery || property?.category !== "system") return false;
  const query = normalizeCanonicalPropertyIdentity(rawQuery);
  return [property?.id, property?.name].some(
    (candidate) => normalizeCanonicalPropertyIdentity(candidate) === query,
  );
};

const getUnambiguousCanonicalSystemMatches = (properties, rawQuery) => {
  const systemMatches = (properties || []).filter((property) =>
    propertyMatchesCanonicalSystemIdentity(property, rawQuery),
  );
  const hasDistinctRawAttribute = (properties || []).some(
    (property) =>
      property?.category === "attribute" &&
      propertyMatchesRawId(property, rawQuery),
  );
  return systemMatches.length === 1 && !hasDistinctRawAttribute
    ? systemMatches
    : [];
};

export function filterPropertiesForPicker({
  properties,
  category = "all",
  search = "",
  hasCategorySidebar = true,
}) {
  const rawQuery = String(search || "").trim();
  const query = normalizePropertySearchText(search);
  let list = properties || [];
  // Category selection remains authoritative while users refine a search;
  // otherwise System rows leak into Attributes (and vice versa).
  if (hasCategorySidebar && category !== "all") {
    list = list.filter((property) => property.category === category);
  }
  if (!query) return list;
  const rawIdMatches = list.filter((property) =>
    propertyMatchesRawId(property, rawQuery),
  );
  const canonicalSystemMatches = getUnambiguousCanonicalSystemMatches(
    list,
    rawQuery,
  );
  const fuzzyMatches = list.filter((property) => {
    const name = normalizePropertySearchText(property.name);
    const id = normalizePropertySearchText(property.id);
    const aliases = (property.searchAliases || []).some((alias) =>
      normalizePropertySearchText(alias).includes(query),
    );
    return name.includes(query) || id.includes(query) || aliases;
  });
  // Exact ids and canonical System labels stay first, but All must retain all
  // fuzzy category matches. For example, `cost` must show Cost plus every
  // matching cost_breakdown attribute instead of collapsing to one System
  // row merely because its id is exact.
  const preferredMatches = [
    ...new Set([...rawIdMatches, ...canonicalSystemMatches]),
  ];
  const exactMatches = new Set(preferredMatches);
  return [
    ...preferredMatches,
    ...fuzzyMatches.filter((property) => !exactMatches.has(property)),
  ];
}

// Attribute discovery is deliberately bounded. If the exact lookup cannot
// find a key (or is temporarily unavailable), users must still be able to
// enter a known key without broadening the backend read. The fallback is
// string-typed; a successful exact lookup always wins and preserves the
// backend-provided number/boolean/array/map type.
// eslint-disable-next-line react-refresh/only-export-components
export function buildManualAttributeProperty({
  search,
  category,
  properties,
  enabled = true,
  hasCategorySidebar = true,
}) {
  const exactName = String(search || "").trim();
  if (
    !enabled ||
    !hasCategorySidebar ||
    (category !== "all" && category !== "attribute") ||
    !exactName ||
    exactName.length > 512
  ) {
    return null;
  }
  if (
    (properties || []).some(
      (property) =>
        property.category === "attribute" && property.id === exactName,
    )
  ) {
    return null;
  }
  return {
    id: exactName,
    registryId: `custom_attribute:${exactName}`,
    name: exactName,
    category: "attribute",
    rawCategory: "custom_attribute",
    type: "string",
    apiColType: "SPAN_ATTRIBUTE",
    isManualExactAttribute: true,
  };
}

const resolveFieldCategory = (explicitCategory, prop, fallback = "system") => {
  if (explicitCategory !== undefined) return explicitCategory;
  if (prop) return prop.category;
  return fallback;
};

// A backend field id is only unique inside its column type/category. This
// lookup keeps a raw attribute such as `cost_cents` bound to its Attribute
// metadata even when a canonical System field has the same id.
const PROPERTY_CATEGORY_TO_API_COL_TYPE = {
  system: "SYSTEM_METRIC",
  attribute: "SPAN_ATTRIBUTE",
  eval: "EVAL_METRIC",
  annotation: "ANNOTATION",
};

export function findTraceFilterProperty(properties, filter) {
  const registryId = filter?.registryId || filter?.property_id;
  if (registryId) {
    const exactRegistryMatch = (properties || []).find(
      (property) =>
        (property.registryId || property.property_id) === registryId,
    );
    if (exactRegistryMatch) return exactRegistryMatch;
  }
  let matches = (properties || []).filter(
    (property) => property.id === filter?.field,
  );
  if (filter?.apiColType) {
    matches = matches.filter(
      (property) =>
        (property.apiColType ||
          PROPERTY_CATEGORY_TO_API_COL_TYPE[property.category]) ===
        filter.apiColType,
    );
  }
  if (filter?.fieldCategory) {
    matches = matches.filter(
      (property) => property.category === filter.fieldCategory,
    );
  }
  return matches[0];
}

const queryPropertyIdentity = (property) =>
  `__field_identity__${
    property.registryId ||
    JSON.stringify([
      property.apiColType || "",
      property.category || "",
      property.id,
    ])
  }`;

export function mergeCatalogPropertyPages(...propertyLists) {
  const propertiesByIdentity = new Map();
  for (const property of propertyLists.flat()) {
    if (!property?.id) continue;
    propertiesByIdentity.set(queryPropertyIdentity(property), property);
  }
  return [...propertiesByIdentity.values()];
}

const isCatalogSearchFallbackProperty = (property) =>
  property?.catalogSearchFallback === true;

const ownedGlobalPropertyIds = (property) =>
  new Set(
    [
      property?.id,
      property?.responseKey,
      ...(property?.dynamicAliases || []),
      ...(property?.legacyWireValues || []),
    ]
      .map(normalizeCanonicalPropertyIdentity)
      .filter(Boolean),
  );

const representsGlobalProperty = (candidate, globalProperty) => {
  if (
    queryPropertyIdentity(candidate) === queryPropertyIdentity(globalProperty)
  ) {
    return true;
  }
  if (!isCatalogSearchFallbackProperty(globalProperty)) return false;
  // Alias ownership never crosses picker families. A raw attribute can use
  // the same backend key as a canonical System field (for example
  // `gen_ai.usage.total_tokens`), but it must remain a separate Attribute
  // result rather than suppressing the local System definition.
  if (
    candidate?.category &&
    globalProperty?.category &&
    candidate.category !== globalProperty.category
  ) {
    return false;
  }
  const candidateIds = ownedGlobalPropertyIds(candidate);
  return [...ownedGlobalPropertyIds(globalProperty)].some((id) =>
    candidateIds.has(id),
  );
};

const matchingGlobalSearchProperties = ({
  baseProperties,
  search,
  category = "all",
  hasCategorySidebar = true,
}) =>
  filterPropertiesForPicker({
    properties: (baseProperties || []).filter(isCatalogSearchFallbackProperty),
    category,
    search,
    hasCategorySidebar,
  });

const supplementalGlobalSearchProperties = ({
  baseProperties,
  catalogProperties,
  search,
  category = "all",
  hasCategorySidebar = true,
}) =>
  matchingGlobalSearchProperties({
    baseProperties,
    search,
    category,
    hasCategorySidebar,
  }).filter(
    (localProperty) =>
      !(catalogProperties || []).some((catalogProperty) =>
        representsGlobalProperty(catalogProperty, localProperty),
      ),
  );

// Project catalog search intentionally owns project-specific results, but its
// persisted index does not contain frontend-synthetic/global system fields.
// Retain only matching global fields from the already-authoritative base
// inventory; merging the whole base list would resurrect stale attributes and
// evals that the server search correctly excluded.
// eslint-disable-next-line react-refresh/only-export-components
export function mergeCatalogSearchProperties({
  baseProperties,
  catalogProperties,
  search,
  category = "all",
  hasCategorySidebar = true,
}) {
  const localMatches = matchingGlobalSearchProperties({
    baseProperties,
    search,
    category,
    hasCategorySidebar,
  });
  const catalogOwnedLocalMatches = new Set();
  const authoritativeCatalogMatches = (catalogProperties || []).filter(
    (catalogProperty) => {
      const localProperty = localMatches.find((candidate) =>
        representsGlobalProperty(catalogProperty, candidate),
      );
      if (!localProperty) return true;

      // An exact catalog id remains authoritative for project naming/type
      // metadata. Alias-only results (for example `tokens`) yield to the local
      // canonical definition so filters still submit
      // `gen_ai.usage.total_tokens` and retain local transport metadata.
      if (
        normalizeCanonicalPropertyIdentity(catalogProperty.id) ===
        normalizeCanonicalPropertyIdentity(localProperty.id)
      ) {
        catalogOwnedLocalMatches.add(localProperty);
        return true;
      }
      return false;
    },
  );
  return mergeCatalogPropertyPages(
    localMatches.filter((property) => !catalogOwnedLocalMatches.has(property)),
    authoritativeCatalogMatches,
  );
}

const PROPERTY_CATEGORY_COUNT_KEY = {
  system: "system_metric",
  eval: "eval_metric",
  annotation: "annotation_metric",
  attribute: "custom_attribute",
  dataset: "custom_column",
};

// eslint-disable-next-line react-refresh/only-export-components
export function supplementCatalogSearchCategoryCounts({
  categoryCounts,
  baseProperties,
  catalogProperties,
  search,
}) {
  if (!categoryCounts) return categoryCounts;
  const supplements = supplementalGlobalSearchProperties({
    baseProperties,
    catalogProperties,
    search,
    category: "all",
    hasCategorySidebar: true,
  });
  if (supplements.length === 0) return categoryCounts;

  const nextCounts = { ...categoryCounts };
  for (const property of supplements) {
    const categoryKey = PROPERTY_CATEGORY_COUNT_KEY[property.category];
    if (
      !categoryKey ||
      !Number.isSafeInteger(nextCounts.all) ||
      !Number.isSafeInteger(nextCounts[categoryKey])
    ) {
      return categoryCounts;
    }
    nextCounts.all += 1;
    nextCounts[categoryKey] += 1;
  }
  return nextCounts;
}

export function buildQueryPropertyEntries(properties) {
  return {
    entries: (properties || []).map((property) => [
      queryPropertyIdentity(property),
      property,
    ]),
  };
}

const ANNOTATOR_FILTER_PROPERTY = {
  id: "annotator",
  registryId: "annotation:annotator",
  name: "Annotator",
  category: "annotation",
  rawCategory: "annotation_metric",
  type: "annotator",
  // This is visually grouped with annotation filters, but the backend treats
  // column_id=annotator as a global Score annotator filter, not a label column.
  apiColType: "SYSTEM_METRIC",
  allowCustomValue: false,
  catalogSearchFallback: true,
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
  const choices =
    type === "thumbs"
      ? ["Thumbs Up", "Thumbs Down"]
      : m.choiceOptions || m.choice_options || m.choices;
  const apiColType = isEval
    ? "EVAL_METRIC"
    : isAnnotation
      ? "ANNOTATION"
      : m.category === "system_metric" || m.category === "systemMetric"
        ? "SYSTEM_METRIC"
        : "SPAN_ATTRIBUTE";
  return {
    id: m.name,
    registryId:
      m.propertyId ||
      m.property_id ||
      `${
        isEval
          ? "eval"
          : isAnnotation
            ? "annotation"
            : apiColType === "SYSTEM_METRIC"
              ? "system_attribute"
              : "custom_attribute"
      }:${apiColType === "SYSTEM_METRIC" ? `${m.source || "all"}:` : ""}${m.name}`,
    name: m.displayName || m.display_name || m.name,
    category: mapCategory(m.category),
    rawCategory: m.category,
    type,
    outputType,
    choices,
    apiColType,
    attributeTypes: m.attributeTypes || m.attribute_types,
    attributeTypesExact:
      m.attributeTypesExact ?? m.attribute_types_exact ?? false,
  };
}

export function buildTraceFilterProperties(
  metrics,
  {
    isSimulator = false,
    sourceScope = null,
    includeGlobalAnnotator = false,
  } = {},
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
      const sourceTokens = new Set([src, ...sources].filter(Boolean));
      const voiceCallTraceFamily =
        sourceScope === "voice_calls" &&
        sourceTokens.has("traces") &&
        [
          "eval_metric",
          "evalMetric",
          "annotation_metric",
          "annotationMetric",
          "custom_attribute",
          "customAttribute",
        ].includes(cat);
      const sourceCompatible =
        !sourceScope ||
        sourceTokens.has("all") ||
        sourceTokens.has("both") ||
        sourceTokens.has(sourceScope) ||
        (sourceScope === "spans" && sourceTokens.has("traces")) ||
        voiceCallTraceFamily;

      // Always exclude blacklisted metrics
      if (EXCLUDED_METRICS.has(name)) return false;

      // Exclude dataset-only metrics
      if (src === "datasets") return false;

      // A catalog page can be reused by older/fallback consumers, so retain a
      // defensive source check in addition to the server-side source filter.
      // In particular, simulator projects also have trace pages: simulation
      // definitions must never be offered there and then submitted through
      // source=traces, which the API correctly rejects as an invalid binding.
      if (!sourceCompatible) return false;

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

  // The base inventory owns global synthetic fields even when its first
  // catalog page has no annotation definitions. Search-scoped pages leave the
  // flag disabled so they cannot inject Annotator into an unrelated search.
  const globalAnnotatorSupported = !["dataset", "simulation"].includes(
    sourceScope,
  );
  if (
    !alreadyHasAnnotator &&
    globalAnnotatorSupported &&
    (firstAnnotationIndex !== -1 || includeGlobalAnnotator)
  ) {
    properties.splice(
      firstAnnotationIndex === -1 ? properties.length : firstAnnotationIndex,
      0,
      ANNOTATOR_FILTER_PROPERTY,
    );
  }

  return properties;
}

export function useLegacyTraceFilterProperties(
  projectId,
  {
    enabled = true,
    isSimulator = false,
    sourceScope = null,
    allowWorkspaceScope = false,
  } = {},
) {
  const requestScope = projectId || (allowWorkspaceScope ? "workspace" : "");
  const query = useInfiniteQuery({
    // Key on request scope only — isSimulator/sourceScope affect only the
    // per-observer `select`, not the request, so keying on them duplicated fetches.
    queryKey: ["trace-filter-properties-v2", requestScope],
    enabled: enabled && Boolean(requestScope),
    queryFn: async ({ pageParam = 1, signal }) => {
      const params = {
        page: pageParam,
        page_size: PROPERTY_CATALOG_LEGACY_PAGE_SIZE,
      };
      if (projectId) params.project_ids = projectId;
      // Observe filter dropdown wants per-CustomEvalConfig eval entries (so
      // the dropdown matches the per-config columns in the trace/span list
      // table). Default behaviour at /tracer/dashboard/metrics/ is still
      // template-level — used by dashboards, PrimaryGraph, widget pickers.
      params.per_eval_config = true;
      params.exclude_custom_attributes = true;
      const { data } = await axios.get(endpoints.dashboard.metrics, {
        params,
        signal,
        timeout: PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
      });
      return data?.result || { metrics: [], has_more: false };
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage) =>
      lastPage?.has_more === true ? Number(lastPage.page || 1) + 1 : undefined,
    staleTime: PROPERTY_CATALOG_LEGACY_STALE_TIME_MS,
    gcTime: PROPERTY_CATALOG_LEGACY_CACHE_TIME_MS,
    meta: { errorHandled: true },
  });
  const metrics = (query.data?.pages || []).flatMap(
    (page) => page?.metrics || [],
  );
  return {
    ...query,
    data: buildTraceFilterProperties(metrics, {
      isSimulator,
      sourceScope,
      includeGlobalAnnotator: true,
    }),
    continuationKey: query.hasNextPage
      ? `legacy-page:${Number(query.data?.pages?.at(-1)?.page || 1) + 1}`
      : null,
    pageCount: query.data?.pages?.length || 0,
  };
}

export function useTraceFilterProperties(
  projectId,
  {
    enabled = true,
    isSimulator = false,
    sourceScope = null,
    search = "",
    allowWorkspaceScope = false,
  } = {},
) {
  const hasCatalogScope = Boolean(projectId || allowWorkspaceScope);
  const fallbackScopeKey = JSON.stringify([
    "trace-filter-property-catalog",
    projectId || (allowWorkspaceScope ? "workspace" : ""),
    sourceScope || "",
  ]);
  const catalog = usePropertyCatalog({
    projectIds: projectId ? [projectId] : [],
    source: sourceScope || "",
    search,
    perEvalConfig: true,
    pageSize: PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
    enabled: enabled && hasCatalogScope,
    // The legacy definition endpoint is organization-scoped when project_ids
    // is omitted. Keep it dormant unless the typed catalog explicitly reports
    // not-ready, then use it as a bounded compatibility read for Users pages.
    allowLegacyNotReadyFallback: Boolean(projectId || allowWorkspaceScope),
    fallbackScopeKey,
  });
  const legacy = useLegacyTraceFilterProperties(projectId, {
    enabled: enabled && catalog.legacyFallbackRequired,
    isSimulator,
    sourceScope,
    allowWorkspaceScope,
  });

  if (catalog.legacyFallbackRequired) {
    return { ...legacy, usesUnifiedCatalog: false };
  }

  const metrics = catalog.metrics;
  const catalogNotReady = isPropertyCatalogNotReadyError(catalog.error);
  const catalogError = Boolean(catalog.isError && !catalogNotReady);
  return {
    ...catalog,
    data: buildTraceFilterProperties(metrics, {
      isSimulator,
      sourceScope,
      includeGlobalAnnotator: true,
    }),
    usesUnifiedCatalog: true,
    categoryCounts: catalog.categoryCounts,
    categoryCountsExact: catalog.categoryCountsExact,
    isLoading: catalog.isLoading || catalogNotReady,
    isError: catalogError,
    error: catalogError ? catalog.error : null,
    isSuccess: catalog.isSuccess && !catalog.cursorChainStopped,
    hasNextPage: Boolean(catalog.hasNextPage),
    isFetchNextPageError: Boolean(
      catalog.isFetchNextPageError || catalog.cursorChainStopped,
    ),
  };
}

export function useLegacyTraceAttributeProperties(
  projectId,
  {
    enabled = true,
    source = "traces",
    search = "",
    allowWorkspaceScope = false,
    isSimulator = false,
    propertyFilter,
  } = {},
) {
  const hasScope = Boolean(projectId || allowWorkspaceScope);
  const query = useLegacyDashboardMetricsPaginated({
    category: "custom_attribute",
    source,
    search,
    projectIds: projectId ? [projectId] : [],
    perEvalConfig: true,
    pageSize: PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
    enabled: enabled && hasScope,
  });
  const data = useMemo(() => {
    const properties = buildTraceFilterProperties(query.metrics || [], {
      isSimulator,
      sourceScope: source,
    });
    return propertyFilter ? properties.filter(propertyFilter) : properties;
  }, [isSimulator, propertyFilter, query.metrics, source]);

  return {
    ...query,
    data,
    queryReadState: query.isError
      ? "error"
      : query.isSuccess
        ? "complete"
        : "loading",
    browseStatus: query.hasNextPage ? "continuation" : "exhausted",
    totalCount: query.total,
  };
}

// ---------------------------------------------------------------------------
// PropertyPicker — dashboard-style two-column picker
// ---------------------------------------------------------------------------
export function mergeRetainedAttributeProperties(
  properties,
  retainedAttributeProperties,
  { canonical = false } = {},
) {
  const catalog = properties || [];
  const nonAttributes = catalog.filter(
    (property) => property.category !== "attribute",
  );
  // Field id alone is not an identity: a raw span attribute may legitimately
  // have the same key as a system metric (for example `cost_cents`). Keep one
  // retained Attribute entry per raw id without reserving System ids.
  const retainedAttributes = Array.from(
    new Map(
      (retainedAttributeProperties || [])
        .filter((property) => property.category === "attribute")
        .map((property) => [property.id, property]),
    ).values(),
  );
  const retainedIds = new Set(
    retainedAttributes.map((property) => property.id),
  );
  const catalogAttributeFallback = canonical
    ? []
    : catalog.filter(
        (property) =>
          property.category === "attribute" && !retainedIds.has(property.id),
      );
  return [...nonAttributes, ...retainedAttributes, ...catalogAttributeFallback];
}

export function shouldUseRetainedAttributePages({
  enabled,
  source,
  readState,
  attributes,
  browseStatus,
}) {
  const supportedSource = source === "traces" || source === "spans";
  const hasAuthoritativeKeys = (attributes?.length || 0) > 0;
  const inventoryIsTerminal = browseStatus === "exhausted";

  // An empty continuation page only proves that its bounded physical slices
  // contained no attributes. Keep the compatibility catalog visible until
  // the retained-data walk yields a key or reaches a terminal state.
  return (
    enabled &&
    supportedSource &&
    readState === "complete" &&
    (hasAuthoritativeKeys || inventoryIsTerminal)
  );
}

export function PropertyPickerPaginationControl({
  scrollRootRef,
  resetKey,
  autoAdvanceWhileVisible,
  attributePageAvailable,
  attributeContinuationKey,
  isFetchingAttributePage,
  attributePageError,
  onLoadMoreAttributes,
  catalogPageAvailable,
  catalogContinuationKey,
  isFetchingCatalogPage,
  catalogPageError,
  onLoadMoreCatalog,
}) {
  return (
    <BoundedCursorPaginationControl
      rootRef={scrollRootRef}
      resetKey={resetKey}
      autoAdvanceWhileVisible={autoAdvanceWhileVisible}
      requireUserAdvanceGesture
      channels={[
        {
          channelKey: "attributes",
          hasNextPage: attributePageAvailable,
          continuationKey: attributeContinuationKey,
          isFetching: isFetchingAttributePage,
          error: attributePageError,
          loadNextPage: onLoadMoreAttributes,
        },
        {
          channelKey: "catalog",
          hasNextPage: catalogPageAvailable,
          continuationKey: catalogContinuationKey,
          isFetching: isFetchingCatalogPage,
          error: catalogPageError,
          loadNextPage: onLoadMoreCatalog,
        },
      ]}
      loadingLabel="Loading next property catalog page…"
      retryLabel={
        catalogPageError && !attributePageError
          ? "Retry loading properties"
          : "Retry loading attributes"
      }
      markerProps={{
        "data-filter-property-page-sentinel": true,
      }}
    />
  );
}

function PropertyPicker({
  anchorEl,
  open,
  onClose,
  properties,
  onSelect,
  categories = CATEGORIES,
  projectId,
  allowWorkspaceScope = false,
  source = "traces",
  enableExactAttributeLookup = true,
  unifiedCatalogActive = false,
  isSimulator = false,
  catalogError = false,
  hasNextCatalogPage = false,
  catalogContinuationKey = null,
  isFetchingNextCatalogPage = false,
  catalogNextPageError = false,
  loadNextCatalogPage,
  catalogCategoryCounts = null,
  catalogCategoryCountsExact = false,
  propertyFilter,
}) {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const autoExactSearchIdentityRef = useRef(null);
  const propertyOptionsListRef = useRef(null);
  const [visiblePropertyLimit, setVisiblePropertyLimit] = useState(
    PROPERTY_PICKER_RENDER_BATCH_SIZE,
  );
  const hasCategorySidebar = categories && categories.length > 0;
  const trimmedSearch = search.trim();
  const debouncedCatalogSearch = useDebounce(
    trimmedSearch,
    PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS,
  );
  const catalogSearchSettled = debouncedCatalogSearch === trimmedSearch;
  const selectedCatalogCategory =
    {
      system: "system_metric",
      eval: "eval_metric",
      annotation: "annotation_metric",
      attribute: "custom_attribute",
    }[category] || "";
  const pickerCatalogFallbackScope = JSON.stringify([
    "trace-filter-property-picker",
    projectId || (allowWorkspaceScope ? "workspace" : ""),
    source,
  ]);
  const unifiedCatalogScopeActive = Boolean(
    unifiedCatalogActive &&
      open &&
      (projectId || allowWorkspaceScope) &&
      (debouncedCatalogSearch || selectedCatalogCategory),
  );
  const searchedCatalog = usePropertyCatalog({
    category: selectedCatalogCategory,
    projectIds: projectId ? [projectId] : [],
    source,
    search: debouncedCatalogSearch,
    perEvalConfig: true,
    pageSize: PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
    enabled: unifiedCatalogScopeActive,
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: `${pickerCatalogFallbackScope}:scoped`,
  });
  // Keep the All-search definition/count request mounted independently from
  // category navigation. React Query deduplicates this request while All is
  // selected; after a category click it prevents the scoped response from
  // replacing the search-wide sidebar totals.
  const allSearchCatalog = usePropertyCatalog({
    projectIds: projectId ? [projectId] : [],
    source,
    search: debouncedCatalogSearch,
    perEvalConfig: true,
    pageSize: PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
    enabled: Boolean(
      unifiedCatalogActive &&
        open &&
        (projectId || allowWorkspaceScope) &&
        debouncedCatalogSearch,
    ),
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: `${pickerCatalogFallbackScope}:all-search`,
  });
  const searchedCatalogProperties = useMemo(() => {
    const catalogProperties = buildTraceFilterProperties(
      searchedCatalog.metrics || [],
      {
        isSimulator,
        sourceScope: source,
      },
    );
    return propertyFilter
      ? catalogProperties.filter(propertyFilter)
      : catalogProperties;
  }, [isSimulator, propertyFilter, searchedCatalog.metrics, source]);
  const allSearchCatalogProperties = useMemo(() => {
    const catalogProperties = buildTraceFilterProperties(
      allSearchCatalog.metrics || [],
      {
        isSimulator,
        sourceScope: source,
      },
    );
    return propertyFilter
      ? catalogProperties.filter(propertyFilter)
      : catalogProperties;
  }, [allSearchCatalog.metrics, isSimulator, propertyFilter, source]);
  const allSearchCatalogHasExactCounts = Boolean(
    allSearchCatalog.categoryCountsExact && allSearchCatalog.categoryCounts,
  );
  const searchedCatalogOwnsResults = Boolean(
    unifiedCatalogScopeActive &&
      catalogSearchSettled &&
      !searchedCatalog.legacyFallbackRequired,
  );
  const unifiedCatalogSearchPending = Boolean(
    unifiedCatalogActive &&
      open &&
      (projectId || allowWorkspaceScope) &&
      trimmedSearch &&
      !catalogSearchSettled,
  );
  const effectiveCatalogProperties = useMemo(
    () =>
      searchedCatalogOwnsResults
        ? mergeCatalogSearchProperties({
            baseProperties: properties,
            catalogProperties: searchedCatalogProperties,
            search: trimmedSearch,
            category,
            hasCategorySidebar,
          })
        : properties,
    [
      category,
      hasCategorySidebar,
      properties,
      searchedCatalogOwnsResults,
      searchedCatalogProperties,
      trimmedSearch,
    ],
  );
  const supplementedAllSearchCategoryCounts = useMemo(
    () =>
      supplementCatalogSearchCategoryCounts({
        categoryCounts: allSearchCatalog.categoryCounts,
        baseProperties: properties,
        catalogProperties: allSearchCatalogProperties,
        search: trimmedSearch,
      }),
    [
      allSearchCatalog.categoryCounts,
      allSearchCatalogProperties,
      properties,
      trimmedSearch,
    ],
  );
  const searchCountsOwnSidebar = Boolean(
    trimmedSearch &&
      catalogSearchSettled &&
      searchedCatalogOwnsResults &&
      allSearchCatalogHasExactCounts,
  );
  const searchCountsPending = Boolean(
    trimmedSearch &&
      catalogSearchSettled &&
      searchedCatalogOwnsResults &&
      !allSearchCatalogHasExactCounts,
  );
  // Category navigation only changes the visible result page. Search-wide
  // counts always come from the independent All-search request; until that
  // exact response arrives, hide the counts instead of displaying a scoped
  // category breakdown that would visibly drift.
  const effectiveCatalogCategoryCounts = searchCountsOwnSidebar
    ? supplementedAllSearchCategoryCounts
    : searchCountsPending
      ? null
      : catalogCategoryCounts;
  const effectiveCatalogCategoryCountsExact = searchCountsOwnSidebar
    ? true
    : searchCountsPending
      ? false
      : catalogCategoryCountsExact;
  const effectiveCatalogError = searchedCatalogOwnsResults
    ? Boolean(searchedCatalog.isError || searchedCatalog.cursorChainStopped)
    : catalogError;
  const effectiveHasNextCatalogPage = searchedCatalogOwnsResults
    ? Boolean(searchedCatalog.hasNextPage)
    : hasNextCatalogPage;
  const effectiveCatalogContinuationKey = searchedCatalogOwnsResults
    ? searchedCatalog.continuationKey
    : catalogContinuationKey;
  const effectiveIsFetchingNextCatalogPage = searchedCatalogOwnsResults
    ? searchedCatalog.isRemoteCatalogNextPagePending ??
      searchedCatalog.isFetchingNextPage
    : isFetchingNextCatalogPage;
  const effectiveCatalogNextPageError = searchedCatalogOwnsResults
    ? Boolean(
        searchedCatalog.isFetchNextPageError ||
          searchedCatalog.cursorChainStopped,
      )
    : catalogNextPageError;
  const effectiveLoadNextCatalogPage = searchedCatalogOwnsResults
    ? searchedCatalog.fetchNextPage
    : loadNextCatalogPage;
  const unifiedCatalogSearchLoading = Boolean(
    unifiedCatalogSearchPending ||
      (unifiedCatalogScopeActive &&
        !searchedCatalog.legacyFallbackRequired &&
        (searchedCatalog.isRemoteCatalogSearchPending ||
          searchedCatalog.isLoading)),
  );
  const legacyAttributeFallbackActive = Boolean(
    open &&
      (projectId || allowWorkspaceScope) &&
      (source === "traces" || source === "spans") &&
      ((searchedCatalog.legacyFallbackRequired && catalogSearchSettled) ||
        (!unifiedCatalogActive &&
          (allowWorkspaceScope ||
            !enableExactAttributeLookup ||
            Boolean(debouncedCatalogSearch)))),
  );
  const legacyAttributeFallback = useLegacyTraceAttributeProperties(projectId, {
    enabled: legacyAttributeFallbackActive,
    source,
    search: debouncedCatalogSearch,
    allowWorkspaceScope,
    isSimulator,
    propertyFilter,
  });
  const {
    data: exactAttributeProperties,
    isFetching: exactAttributeLoading,
    fetchNextPage: fetchNextAttributePage,
    hasNextPage: hasNextAttributePage,
    isFetchingNextPage: isFetchingNextAttributePage,
    fetchNextExactPage: fetchNextExactAttributePage,
    hasNextExactPage: hasNextExactAttributePage = false,
    isFetchingExactSearch = false,
    isFetchingNextExactPage = false,
    isFetchNextPageError: isNextAttributePageError,
    exactSearchError: exactAttributeSearchError,
    queryReadState: exactAttributeReadState,
    browseStatus: exactAttributeBrowseStatus,
    totalCount: exactAttributeTotalCount = null,
    exactSearchMatched: exactAttributeSearchMatched = false,
    cursorRetryExhausted: exactAttributeCursorRetryExhausted = false,
    continuationKey: exactAttributeContinuationKey = null,
    debouncedSearch,
    refetch: refetchAttributePages,
  } = useExactTraceAttributeProperties({
    projectId,
    search,
    source,
    // The activated unified catalog already owns category browsing, text
    // search, and pagination. Running the retained-attribute hook alongside
    // it issues a second category=custom_attribute request every time the
    // picker opens and can make the UI look stuck behind the slower request.
    // Keep this hook exclusively as the rolling-deploy fallback.
    enabled: enableExactAttributeLookup && open && !unifiedCatalogActive,
  });
  const legacyAttributeFallbackOwnsInventory = Boolean(
    legacyAttributeFallbackActive &&
      (legacyAttributeFallback.isSuccess || legacyAttributeFallback.isError),
  );
  const effectiveAttributeProperties = useMemo(
    () =>
      mergeRetainedAttributeProperties(
        exactAttributeProperties,
        legacyAttributeFallback.data,
      ),
    [exactAttributeProperties, legacyAttributeFallback.data],
  );
  const effectiveAttributeLoading = legacyAttributeFallbackOwnsInventory
    ? legacyAttributeFallback.isLoading
    : exactAttributeLoading;
  const effectiveAttributeReadState = legacyAttributeFallbackOwnsInventory
    ? legacyAttributeFallback.queryReadState
    : exactAttributeReadState;
  const effectiveAttributeBrowseStatus = legacyAttributeFallbackOwnsInventory
    ? legacyAttributeFallback.browseStatus
    : exactAttributeBrowseStatus;
  const effectiveAttributeTotalCount = legacyAttributeFallbackOwnsInventory
    ? legacyAttributeFallback.totalCount
    : exactAttributeTotalCount;
  const effectiveHasNextAttributePage = legacyAttributeFallbackOwnsInventory
    ? legacyAttributeFallback.hasNextPage
    : hasNextAttributePage;
  const effectiveAttributeContinuationKey = legacyAttributeFallbackOwnsInventory
    ? legacyAttributeFallback.continuationKey
    : exactAttributeContinuationKey;
  const effectiveIsFetchingNextAttributePage =
    legacyAttributeFallbackOwnsInventory
      ? legacyAttributeFallback.isFetchingNextPage
      : isFetchingNextAttributePage;
  const effectiveNextAttributePageError = legacyAttributeFallbackOwnsInventory
    ? Boolean(
        legacyAttributeFallback.isError ||
          legacyAttributeFallback.isFetchNextPageError,
      )
    : isNextAttributePageError;
  const effectiveFetchNextAttributePage = legacyAttributeFallbackOwnsInventory
    ? legacyAttributeFallback.fetchNextPage
    : fetchNextAttributePage;
  const effectiveRefetchAttributePages = legacyAttributeFallbackOwnsInventory
    ? legacyAttributeFallback.refetch
    : refetchAttributePages;
  const hasSettledExactAttributeError = Boolean(
    !legacyAttributeFallbackOwnsInventory &&
      search.trim() &&
      debouncedSearch === search.trim() &&
      exactAttributeSearchError,
  );
  const hasAttributePageError =
    effectiveNextAttributePageError || hasSettledExactAttributeError;

  useEffect(() => {
    if (!open) {
      autoExactSearchIdentityRef.current = null;
      setSearch("");
      setCategory("all");
    }
  }, [open]);

  useEffect(() => {
    // The same text can be searched again without closing the picker
    // (foo -> clear/other -> foo). Each settled search gesture owns one
    // bounded automatic continuation; retaining the old identity here made
    // the repeated search look permanently stuck at its cached checkpoint.
    autoExactSearchIdentityRef.current = null;
  }, [search, debouncedSearch, projectId, source]);

  useEffect(() => {
    setVisiblePropertyLimit(PROPERTY_PICKER_RENDER_BATCH_SIZE);
  }, [open, search, category, projectId, source]);

  const usesRetainedAttributePages = shouldUseRetainedAttributePages({
    enabled:
      legacyAttributeFallbackOwnsInventory ||
      (enableExactAttributeLookup && !unifiedCatalogActive),
    source,
    readState: effectiveAttributeReadState,
    attributes: effectiveAttributeProperties,
    browseStatus: effectiveAttributeBrowseStatus,
  });

  const propertiesWithExactAttribute = useMemo(() => {
    // `/tracer/dashboard/metrics/` still carries a bounded compatibility
    // sample of attributes for older consumers.  Once the cursor endpoint is
    // healthy, it is the canonical picker inventory: re-appending the metrics
    // sample makes a continuation look broken because newly fetched keys were
    // already rendered.  Keep the sample only as a degraded-read fallback.
    return mergeRetainedAttributeProperties(
      effectiveCatalogProperties,
      effectiveAttributeProperties,
      {
        canonical: usesRetainedAttributePages,
      },
    );
  }, [
    effectiveCatalogProperties,
    effectiveAttributeProperties,
    usesRetainedAttributePages,
  ]);

  const filtered = useMemo(
    () =>
      filterPropertiesForPicker({
        properties: propertiesWithExactAttribute,
        category,
        search,
        hasCategorySidebar,
      }),
    [propertiesWithExactAttribute, category, search, hasCategorySidebar],
  );

  const counts = useMemo(() => {
    const c = { all: propertiesWithExactAttribute.length };
    for (const p of propertiesWithExactAttribute)
      c[p.category] = (c[p.category] || 0) + 1;
    if (effectiveCatalogCategoryCountsExact && effectiveCatalogCategoryCounts) {
      c.all = effectiveCatalogCategoryCounts.all;
      c.system = effectiveCatalogCategoryCounts.system_metric;
      c.eval = effectiveCatalogCategoryCounts.eval_metric;
      c.annotation = effectiveCatalogCategoryCounts.annotation_metric;
      c.attribute = effectiveCatalogCategoryCounts.custom_attribute;
      return c;
    }
    const exactLookupOwnsAttributeInventory =
      (legacyAttributeFallbackOwnsInventory || enableExactAttributeLookup) &&
      (source === "traces" || source === "spans");
    if (exactLookupOwnsAttributeInventory) {
      const loadedAttributeCount = c.attribute || 0;
      const nonAttributeCount = c.all - loadedAttributeCount;
      if (
        Number.isSafeInteger(effectiveAttributeTotalCount) &&
        effectiveAttributeTotalCount >= 0
      ) {
        c.attribute = effectiveAttributeTotalCount;
        c.all = nonAttributeCount + effectiveAttributeTotalCount;
      } else {
        // A growing loaded-page count looks exact but changes on every scroll.
        // Keep both affected categories explicitly unknown until the backend
        // publishes an invariant total or the retained cursor exhausts.
        c.attribute = null;
        c.all = null;
      }
    }
    return c;
  }, [
    effectiveCatalogCategoryCounts,
    effectiveCatalogCategoryCountsExact,
    enableExactAttributeLookup,
    effectiveAttributeTotalCount,
    legacyAttributeFallbackOwnsInventory,
    propertiesWithExactAttribute,
    source,
  ]);
  const visibleProperties = filtered.slice(0, visiblePropertyLimit);
  const hiddenCount = Math.max(filtered.length - visiblePropertyLimit, 0);
  const displayedPropertyCount = search.trim()
    ? Number.isSafeInteger(counts[category])
      ? counts[category]
      : filtered.length
    : counts.all;
  const catalogCategoryCanContinue = (
    unifiedCatalogActive
      ? ["all", "system", "eval", "annotation", "attribute"]
      : ["all", "eval", "annotation"]
  ).includes(category);
  const attributeCategoryCanContinue = ["all", "attribute"].includes(category);
  const catalogSearchAlreadyMatched = Boolean(
    search.trim() && filtered.length > 0,
  );
  // An activated catalog owns every category and every matching search page.
  // The compatibility path still keeps attributes on its retained cursor, so
  // unrelated legacy catalog pages stay hidden after a local match.
  const shouldShowCatalogContinuation =
    catalogCategoryCanContinue &&
    (!unifiedCatalogActive || !searchedCatalog.legacyFallbackRequired) &&
    (unifiedCatalogActive || !catalogSearchAlreadyMatched);
  // A successful exact probe stops only that supplemental chain. The retained
  // catalog remains explicitly pageable so sibling substring matches can be
  // discovered without automatically draining it.
  const canLoadNextAttributePage = Boolean(
    effectiveHasNextAttributePage ||
      (!legacyAttributeFallbackOwnsInventory &&
        search.trim() &&
        (hasNextExactAttributePage || hasSettledExactAttributeError)),
  );
  const exactAttributeDiscoveryTerminal = legacyAttributeFallbackOwnsInventory
    ? effectiveAttributeBrowseStatus === "exhausted" ||
      effectiveAttributeReadState === "error"
    : exactAttributeCursorRetryExhausted ||
      exactAttributeBrowseStatus === "exhausted" ||
      exactAttributeReadState === "error" ||
      exactAttributeReadState === "degraded";
  const manualAttributeProperty = useMemo(
    () =>
      debouncedSearch === search.trim() && !effectiveAttributeLoading
        ? buildManualAttributeProperty({
            search,
            category,
            properties: propertiesWithExactAttribute,
            // Do not guess that an unseen retained key is text while the
            // cursor still has older typed pages. A manual future-key entry is
            // offered only after the frozen retained catalog is exhausted.
            enabled:
              enableExactAttributeLookup &&
              exactAttributeDiscoveryTerminal &&
              !effectiveHasNextAttributePage,
            hasCategorySidebar,
          })
        : null,
    [
      category,
      debouncedSearch,
      enableExactAttributeLookup,
      exactAttributeDiscoveryTerminal,
      effectiveAttributeLoading,
      effectiveHasNextAttributePage,
      hasCategorySidebar,
      propertiesWithExactAttribute,
      search,
    ],
  );

  const paperWidth = hasCategorySidebar ? 480 : 320;
  const loadNextAttributePage = useSingleFlightPageRequest({
    identity: JSON.stringify([
      legacyAttributeFallbackOwnsInventory ? "legacy" : "retained",
      projectId || (allowWorkspaceScope ? "workspace" : ""),
      source,
      debouncedSearch,
    ]),
    enabled: canLoadNextAttributePage && !effectiveIsFetchingNextAttributePage,
    request: effectiveFetchNextAttributePage,
  });
  const loadNextExactAttributePage = useSingleFlightPageRequest({
    identity: JSON.stringify(["exact", projectId, source, debouncedSearch]),
    enabled:
      (hasNextExactAttributePage || hasSettledExactAttributeError) &&
      !isFetchingExactSearch &&
      !isFetchingNextExactPage,
    request: fetchNextExactAttributePage,
  });
  const loadNextVisibleAttributePage =
    !legacyAttributeFallbackOwnsInventory &&
    search.trim() &&
    (hasNextExactAttributePage || hasSettledExactAttributeError)
      ? loadNextExactAttributePage
      : loadNextAttributePage;
  const attributePageAvailable =
    attributeCategoryCanContinue && canLoadNextAttributePage;
  const catalogPageAvailable =
    shouldShowCatalogContinuation &&
    !unifiedCatalogSearchPending &&
    effectiveHasNextCatalogPage;
  const isFetchingVisibleAttributePage = Boolean(
    attributeCategoryCanContinue &&
      (effectiveIsFetchingNextAttributePage ||
        (!legacyAttributeFallbackOwnsInventory && isFetchingNextExactPage)),
  );
  useEffect(() => {
    const settledSearch = search.trim();
    if (
      !open ||
      legacyAttributeFallbackOwnsInventory ||
      !settledSearch ||
      debouncedSearch !== settledSearch ||
      exactAttributeSearchMatched ||
      filtered.length > 0 ||
      !hasNextExactAttributePage ||
      isFetchingExactSearch ||
      isFetchingNextExactPage ||
      hasAttributePageError ||
      exactAttributeCursorRetryExhausted ||
      typeof fetchNextExactAttributePage !== "function"
    ) {
      return;
    }
    const identity = JSON.stringify([projectId, source, debouncedSearch]);
    if (autoExactSearchIdentityRef.current === identity) return;
    autoExactSearchIdentityRef.current = identity;
    // One bounded automatic continuation makes an empty checkpoint feel like
    // one search instead of requiring a scroll. Later pages are advanced by
    // the list-end sentinel through the same single-flight request.
    void loadNextExactAttributePage();
  }, [
    debouncedSearch,
    exactAttributeSearchMatched,
    fetchNextExactAttributePage,
    filtered.length,
    hasNextExactAttributePage,
    isFetchingExactSearch,
    isFetchingNextExactPage,
    hasAttributePageError,
    loadNextExactAttributePage,
    legacyAttributeFallbackOwnsInventory,
    open,
    projectId,
    search,
    source,
    exactAttributeCursorRetryExhausted,
  ]);
  const revealNextPropertyBatch = useCallback(() => {
    setVisiblePropertyLimit((current) =>
      Math.min(current + PROPERTY_PICKER_RENDER_BATCH_SIZE, filtered.length),
    );
  }, [filtered.length]);
  const handlePropertyScroll = useCallback(
    (event) => {
      const { scrollTop, clientHeight, scrollHeight } = event.currentTarget;
      const isNearBottom =
        scrollHeight - scrollTop - clientHeight <=
        PROPERTY_PICKER_PREFETCH_MARGIN_PX;
      // Reveal rows already retained in memory. Once those rows are visible,
      // the end sentinel owns server-cursor advancement through the same
      // single-flight request path used by every picker surface.
      if (isNearBottom && hiddenCount > 0) revealNextPropertyBatch();
    },
    [hiddenCount, revealNextPropertyBatch],
  );

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
                endAdornment: Number.isSafeInteger(displayedPropertyCount) && (
                  <InputAdornment position="end">
                    <Typography
                      variant="caption"
                      aria-label="Property search result count"
                      sx={{ color: "text.disabled", fontSize: 11 }}
                    >
                      {displayedPropertyCount}
                    </Typography>
                  </InputAdornment>
                ),
                sx: { fontSize: 13 },
              }}
            />
            {(enableExactAttributeLookup || legacyAttributeFallbackActive) &&
              search.trim() &&
              debouncedSearch === search.trim() &&
              effectiveAttributeReadState !== "complete" && (
                <Typography
                  role="status"
                  sx={{ mt: 0.75, fontSize: 11, color: "warning.main" }}
                >
                  {getAttributeLookupMessage(effectiveAttributeReadState)}
                </Typography>
              )}
            {hasSettledExactAttributeError && (
              <Box
                role="status"
                sx={{ mt: 0.75, display: "flex", alignItems: "center", gap: 1 }}
              >
                <Typography sx={{ fontSize: 11, color: "warning.main" }}>
                  Exact attribute search could not be completed. Retained
                  matches remain available.
                </Typography>
                <Button
                  size="small"
                  onClick={loadNextExactAttributePage}
                  disabled={isFetchingNextExactPage}
                  sx={{ p: 0, minWidth: 0, fontSize: 11, whiteSpace: "nowrap" }}
                >
                  Retry exact attribute search
                </Button>
              </Box>
            )}
            {!search.trim() &&
              (enableExactAttributeLookup || legacyAttributeFallbackActive) &&
              (source === "traces" || source === "spans") &&
              effectiveAttributeReadState !== "complete" &&
              !effectiveAttributeLoading && (
                <Typography
                  role="status"
                  sx={{ mt: 0.75, fontSize: 11, color: "warning.main" }}
                >
                  {getAttributeLookupMessage(effectiveAttributeReadState)}
                </Typography>
              )}
            {effectiveCatalogError && (
              <Typography
                role="status"
                sx={{ mt: 0.75, fontSize: 11, color: "warning.main" }}
              >
                {getQueryReadMessage("error")}
              </Typography>
            )}
            {(enableExactAttributeLookup || legacyAttributeFallbackActive) &&
              (source === "traces" || source === "spans") &&
              effectiveAttributeReadState !== "complete" &&
              !hasAttributePageError &&
              !exactAttributeCursorRetryExhausted &&
              !effectiveAttributeLoading && (
                <Button
                  size="small"
                  onClick={() => effectiveRefetchAttributePages?.()}
                  sx={{ mt: 0.5, px: 0, minWidth: 0, fontSize: 11 }}
                >
                  Retry attribute suggestions
                </Button>
              )}
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
                    {(counts[cat.key] === null ||
                      Number.isSafeInteger(counts[cat.key])) && (
                      <Typography
                        aria-label={
                          counts[cat.key] === null
                            ? `${cat.label} property count unavailable`
                            : `${cat.label} property count`
                        }
                        title={
                          counts[cat.key] === null
                            ? "Exact count is still loading"
                            : undefined
                        }
                        sx={{ fontSize: 10, color: "text.disabled" }}
                      >
                        {counts[cat.key] === null ? "…" : counts[cat.key]}
                      </Typography>
                    )}
                  </Box>
                ))}
              </Box>
            )}
            <Box
              ref={propertyOptionsListRef}
              data-filter-property-options-list
              onScroll={handlePropertyScroll}
              sx={{ flex: 1, overflow: "auto", maxHeight: 280 }}
            >
              {filtered.length === 0 &&
                !manualAttributeProperty &&
                !canLoadNextAttributePage &&
                !effectiveAttributeLoading &&
                !unifiedCatalogSearchLoading && (
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
              {filtered.length === 0 &&
                !manualAttributeProperty &&
                canLoadNextAttributePage &&
                !effectiveAttributeLoading &&
                !unifiedCatalogSearchLoading &&
                !effectiveIsFetchingNextAttributePage && (
                  <Typography
                    role="status"
                    sx={{
                      p: 2,
                      textAlign: "center",
                      fontSize: 12,
                      color: "text.secondary",
                    }}
                  >
                    No matching attribute found yet. Older attributes load
                    automatically at the end of this list.
                  </Typography>
                )}
              {filtered.length === 0 &&
                (effectiveAttributeLoading || unifiedCatalogSearchLoading) &&
                !effectiveIsFetchingNextAttributePage && (
                  <Box
                    role="status"
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 0.75,
                      py: 2,
                    }}
                  >
                    <CircularProgress size={16} />
                    <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
                      {trimmedSearch
                        ? "Searching property catalog…"
                        : "Loading properties…"}
                    </Typography>
                  </Box>
                )}
              {filtered.length > 0 &&
                trimmedSearch &&
                unifiedCatalogSearchLoading && (
                  <Box
                    role="status"
                    aria-label="Searching property catalog"
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 0.75,
                      px: 1.5,
                      py: 0.75,
                      color: "text.secondary",
                    }}
                  >
                    <CircularProgress size={14} />
                    <Typography sx={{ fontSize: 11 }}>
                      Searching property catalog…
                    </Typography>
                  </Box>
                )}
              {visibleProperties.map((prop, idx) => (
                <Box
                  key={`${prop.category}:${prop.id}:${idx}`}
                  data-filter-property-option={prop.id}
                  data-filter-property-category={prop.category}
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
                    contentVisibility: "auto",
                    containIntrinsicSize: "28px",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Typography
                    noWrap
                    title={prop.name}
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
              {manualAttributeProperty && (
                <Box
                  data-filter-property-option={manualAttributeProperty.id}
                  data-filter-property-manual-exact
                  onClick={() => {
                    onSelect(manualAttributeProperty);
                    onClose();
                    setSearch("");
                    setCategory("all");
                  }}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    px: 1.5,
                    py: 0.75,
                    cursor: "pointer",
                    borderTop: filtered.length > 0 ? "1px solid" : "none",
                    borderColor: "divider",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Iconify
                    icon="mdi:plus-circle-outline"
                    width={16}
                    sx={{ color: "primary.main", flexShrink: 0 }}
                  />
                  <Typography noWrap sx={{ fontSize: 12, flex: 1 }}>
                    Use exact attribute:{" "}
                    <strong>{manualAttributeProperty.id}</strong>
                  </Typography>
                  <Chip
                    size="small"
                    variant="outlined"
                    label="text"
                    sx={{ height: 16, fontSize: 9, flexShrink: 0 }}
                  />
                </Box>
              )}
              {hasAttributePageError && !isFetchingNextAttributePage && (
                <Typography
                  role="status"
                  sx={{
                    px: 1.5,
                    pt: 0.75,
                    fontSize: 11,
                    color: "warning.main",
                  }}
                >
                  More attributes could not be loaded. Please retry.
                </Typography>
              )}
              {hiddenCount === 0 && (
                <PropertyPickerPaginationControl
                  resetKey={JSON.stringify([
                    projectId || "workspace",
                    source || "",
                    category,
                    debouncedSearch,
                  ])}
                  // A missing exact key may publish many bounded checkpoints.
                  // Advance only once per real viewport-entry gesture while
                  // searching instead of draining the retained window merely
                  // because an empty sentinel remains visible.
                  autoAdvanceWhileVisible={!search.trim()}
                  scrollRootRef={propertyOptionsListRef}
                  attributePageAvailable={attributePageAvailable}
                  attributeContinuationKey={effectiveAttributeContinuationKey}
                  isFetchingAttributePage={isFetchingVisibleAttributePage}
                  attributePageError={hasAttributePageError}
                  onLoadMoreAttributes={loadNextVisibleAttributePage}
                  catalogPageAvailable={catalogPageAvailable}
                  catalogContinuationKey={effectiveCatalogContinuationKey}
                  isFetchingCatalogPage={
                    shouldShowCatalogContinuation &&
                    effectiveIsFetchingNextCatalogPage
                  }
                  catalogPageError={effectiveCatalogNextPageError}
                  onLoadMoreCatalog={effectiveLoadNextCatalogPage}
                />
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
// Session ids and users use the same retained dashboard cursor as every other
// system dimension. First/last message vocabularies have no current-table
// index that can prove an exhaustive interactive browse, so those fields stay
// honest free-text inputs instead of calling the legacy capped 9.5s endpoint.
const SESSION_FREE_TEXT_FIELDS = new Set(["first_message", "last_message"]);

const FREE_TEXT_NO_OPTIONS_TEXT =
  "No retained values found — type to search, or add an exact value";

// Retain one extra code unit so the picker can report a clear limit error
// instead of silently truncating oversized pasted values. The stored state and
// every outbound search remain bounded even for an arbitrarily large paste.
const boundExactValueSearchInput = (value, maxUtf8Bytes) =>
  String(value ?? "").slice(0, maxUtf8Bytes + 1);

const pickerValueKey = (value, storageType) =>
  getPickerValueIdentity(value, storageType);

function ValuePicker({
  propertyId,
  propertyCategory,
  projectId,
  value = [],
  valueTypes = [],
  onChange,
  source = "traces",
  property,
  singleSelect = false,
}) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, FILTER_VALUE_SEARCH_DEBOUNCE_MS);
  const valueOptionsListRef = useRef(null);

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
  const exactValueMaxUtf8Bytes =
    metricType === "custom_attribute"
      ? TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES
      : FILTER_STRING_MAX_UTF8_BYTES;
  const customSearchValue = search.trim();
  const customSearchValueTooLong =
    getUtf8ByteLength(customSearchValue) > exactValueMaxUtf8Bytes;
  const debouncedValueSearch = debouncedSearch.trim();
  const debouncedValueSearchTooLong =
    getUtf8ByteLength(debouncedValueSearch) > exactValueMaxUtf8Bytes;
  const backendValueSearch = debouncedValueSearchTooLong
    ? ""
    : debouncedValueSearch;

  const isSessionFreeTextField =
    !hasStaticChoices && SESSION_FREE_TEXT_FIELDS.has(propertyId);
  const filterValueSource = filterValueTransportSource(source, metricType);
  const filterValueMetricName = filterValueAdapterMetricName(
    filterValueSource,
    propertyId,
  );
  const filterValuePropertyId = buildPropertyRegistryId({
    propertyId: property?.registryId,
    metricName: filterValueMetricName,
    metricType,
    source: filterValueSource,
  });

  const isIdOnlyField = !hasStaticChoices && ID_ONLY_FIELDS.has(propertyId);

  // Backend search: every non-static cursor-backed vocabulary. A real
  // annotation, annotator, or dynamic eval value outside page one cannot be
  // discovered by client-side filtering alone. Static choices stay local;
  // unindexed session message fields deliberately remain exact free text.
  const usesBackendSearch =
    !hasStaticChoices &&
    (isIdOnlyField ||
      metricType === "custom_attribute" ||
      metricType === "system_metric" ||
      metricType === "annotation_metric" ||
      metricType === "eval_metric");

  // Primary: dashboard API values
  const {
    data: dashboardOptions = [],
    isLoading: dashLoading,
    isError: dashError,
    queryReadState: dashboardReadState,
    browseStatus: dashboardBrowseStatus,
    browseLimitReached: dashboardBrowseLimitReached,
    fetchNextPage: fetchNextDashboardPage,
    hasNextPage: hasNextDashboardPage,
    isFetchingNextPage: isFetchingNextDashboardPage,
    isFetchNextPageError: isNextDashboardPageError,
    continuationKey: dashboardContinuationKey,
    retryFreshPage: retryDashboardOptions,
    isRetryingFreshPage: isRetryingDashboardOptions,
    refetch: refetchDashboardOptions,
  } = useDashboardFilterValues({
    propertyId: filterValuePropertyId,
    metricName: filterValueMetricName,
    metricType,
    projectIds: projectId ? [projectId] : [],
    source: filterValueSource,
    search: usesBackendSearch ? backendValueSearch : "",
    // Keep the transport keyed by settled text, while allowing the hook to
    // detect rapid clear/re-entry gestures that happen inside the debounce
    // interval and recover one cached failed continuation.
    searchGesture:
      usesBackendSearch && !customSearchValueTooLong ? customSearchValue : "",
    pageSize: FILTER_VALUE_PAGE_SIZE,
    attributeType:
      propertyCategory === "attribute"
        ? property?.attributeTypesExact === true &&
          property?.attributeTypes?.length === 1
          ? property?.type === "text"
            ? "string"
            : ["float", "integer"].includes(property?.type)
              ? "number"
              : property?.type
          : undefined
        : undefined,
    enabled:
      !hasStaticChoices &&
      !isSessionFreeTextField &&
      Boolean(anchorEl) &&
      !customSearchValueTooLong &&
      (!isIdOnlyField || Boolean(debouncedSearch)),
  });
  // `exhausted` is the authoritative terminal state. `limit_reached` remains
  // resumable when the hook validated an advancing signed cursor.
  const hasMoreDashboardValues =
    !customSearchValueTooLong &&
    Boolean(hasNextDashboardPage) &&
    dashboardBrowseStatus !== "exhausted";
  const loadNextDashboardValues = useSingleFlightPageRequest({
    identity: JSON.stringify([
      projectId,
      filterValueSource,
      property?.registryId || propertyId,
      metricType,
      usesBackendSearch ? backendValueSearch : "",
    ]),
    enabled: hasMoreDashboardValues && !isFetchingNextDashboardPage,
    request: fetchNextDashboardPage,
  });
  // Source: static choices or the cursor-backed dashboard API. Message fields
  // intentionally publish no suggestions and accept an exact typed value.
  const rawOptions = hasStaticChoices
    ? property.choices
    : isSessionFreeTextField
      ? []
      : dashboardOptions;
  const isCanonicalVoiceStatus =
    propertyId === "call_status" && property?.apiColType === "SYSTEM_METRIC";
  // Keep the picker on the same canonical vocabulary as the rendered voice
  // rows even while an older backend is rolling out. Provider values such as
  // `ended` and `done` must appear once as `completed`, never as raw aliases.
  const options = useMemo(() => {
    if (!isCanonicalVoiceStatus) return rawOptions;
    const seen = new Set();
    return rawOptions.flatMap((option) => {
      const canonical = normalizeVoiceCallStatus(getPickerOptionValue(option));
      if (canonical === "" || canonical == null || seen.has(canonical)) {
        return [];
      }
      seen.add(canonical);
      if (typeof option === "string") return [canonical];
      return [{ ...option, value: canonical, label: canonical }];
    });
  }, [isCanonicalVoiceStatus, rawOptions]);
  const isLoading = customSearchValueTooLong
    ? false
    : hasStaticChoices
      ? false
      : isSessionFreeTextField
        ? false
        : dashLoading;
  const readState = customSearchValueTooLong
    ? "complete"
    : hasStaticChoices
      ? "complete"
      : isSessionFreeTextField
        ? "complete"
        : dashError
          ? "error"
          : dashboardReadState;
  const readMessage = getFilterValueReadMessage(readState);
  const refetchOptions = retryDashboardOptions || refetchDashboardOptions;

  const filtered = useMemo(() => {
    if (customSearchValueTooLong) return [];
    if (!search || isSessionFreeTextField || isIdOnlyField) return options;
    const q = search.toLowerCase();
    return options.filter((o) =>
      getPickerOptionSearchText(o).toLowerCase().includes(q),
    );
  }, [
    options,
    search,
    isSessionFreeTextField,
    isIdOnlyField,
    customSearchValueTooLong,
  ]);

  const selectedValues = useMemo(() => {
    const normalized = normalizePickerValues(value);
    return isCanonicalVoiceStatus
      ? normalizeVoiceCallStatus(normalized)
      : normalized;
  }, [isCanonicalVoiceStatus, value]);
  const selectedValueTypes = useMemo(
    () =>
      selectedValues.map((_, index) =>
        Array.isArray(valueTypes) ? valueTypes[index] : undefined,
      ),
    [selectedValues, valueTypes],
  );

  const selectedIndexFor = useCallback(
    (optionValue, optionType) =>
      selectedValues.findIndex((selectedValue, index) => {
        if (!Object.is(selectedValue, optionValue)) return false;
        const selectedType = selectedValueTypes[index];
        // Legacy/saved filters predate typed value provenance. Treat their
        // scalar as selected until the user makes a typed choice, at which
        // point the storage family is persisted explicitly.
        return !optionType || !selectedType || selectedType === optionType;
      }),
    [selectedValues, selectedValueTypes],
  );

  const toggleValue = useCallback(
    (val) => {
      // Use the shared helper to read the picker option's stable value
      // (handles both string and {value, label} object shapes).
      const optionValue = getPickerOptionValue(val);
      const optionType = getPickerOptionType(val);
      const selectedIndex = selectedIndexFor(optionValue, optionType);
      if (singleSelect) {
        // Clicking the already-selected value clears; clicking a different
        // value replaces — standard single-select dropdown UX.
        onChange(
          selectedIndex >= 0 ? [] : [optionValue],
          selectedIndex >= 0 ? [] : [optionType],
        );
        return;
      }
      if (selectedIndex >= 0) {
        onChange(
          selectedValues.filter((_, index) => index !== selectedIndex),
          selectedValueTypes.filter((_, index) => index !== selectedIndex),
        );
        return;
      }
      onChange(
        [...selectedValues, optionValue],
        [...selectedValueTypes, optionType],
      );
    },
    [
      selectedIndexFor,
      selectedValueTypes,
      selectedValues,
      onChange,
      singleSelect,
    ],
  );

  const searchMatchesExistingOption = options.some((option) =>
    getPickerOptionExactMatches(option).some(
      (matchValue) =>
        matchValue.toLowerCase() === customSearchValue.toLowerCase(),
    ),
  );
  const showCustomValueRow = Boolean(
    property?.allowCustomValue !== false &&
      customSearchValue &&
      !customSearchValueTooLong &&
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
                ? readState === "error" || readState === "degraded"
                  ? "Enter an exact value or retry"
                  : "Search or enter an exact value"
                : singleSelect
                  ? "Select a value..."
                  : "Select values..."}
          </Typography>
        ) : singleSelect ? (
          // Plain text instead of a chip — chips read as "removable token
          // in a list", which mis-signals multi-select.
          (() => {
            const v = selectedValues[0];
            const selectedType = selectedValueTypes[0];
            const match = options.find((o) => {
              const ov = typeof o === "string" ? o : o.value;
              const optionType = getPickerOptionType(o);
              return (
                Object.is(ov, v) &&
                (!selectedType || !optionType || selectedType === optionType)
              );
            });
            const displayLabel =
              (typeof match === "string" ? match : match?.label) ?? String(v);
            return (
              <Typography
                key={pickerValueKey(v, selectedValueTypes[0])}
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
          selectedValues.slice(0, 3).map((v, selectedIndex) => {
            const selectedType = selectedValueTypes[selectedIndex];
            const match = options.find((o) => {
              const ov = typeof o === "string" ? o : o.value;
              const optionType = getPickerOptionType(o);
              return (
                Object.is(ov, v) &&
                (!selectedType || !optionType || selectedType === optionType)
              );
            });
            const displayLabel =
              (typeof match === "string" ? match : match?.label) ?? String(v);
            const secondaryLabel = getPickerOptionSecondaryLabel(match);
            const chipTitle = secondaryLabel
              ? `${displayLabel} (${secondaryLabel})`
              : displayLabel;
            return (
              <Chip
                key={pickerValueKey(v, selectedType)}
                label={displayLabel}
                title={chipTitle}
                size="small"
                onDelete={(e) => {
                  e.stopPropagation();
                  onChange(
                    selectedValues.filter(
                      (_, index) => index !== selectedIndex,
                    ),
                    selectedValueTypes.filter(
                      (_, index) => index !== selectedIndex,
                    ),
                  );
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
            onChange={(e) =>
              setSearch(
                boundExactValueSearchInput(
                  e.target.value,
                  exactValueMaxUtf8Bytes,
                ),
              )
            }
            error={customSearchValueTooLong}
            autoFocus
            inputProps={{ maxLength: exactValueMaxUtf8Bytes + 1 }}
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
        <Box
          ref={valueOptionsListRef}
          data-filter-value-options-list
          sx={{ maxHeight: 220, overflow: "auto" }}
        >
          {customSearchValueTooLong && (
            <Typography
              role="alert"
              sx={{ px: 1.5, py: 1, fontSize: 11, color: "error.main" }}
            >
              Exact values are limited to {exactValueMaxUtf8Bytes / 1024} KiB (
              {exactValueMaxUtf8Bytes.toLocaleString("en-US")} UTF-8 bytes).
              Shorten this value to search or apply it.
            </Typography>
          )}
          {isLoading && (
            <Box
              role="status"
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 0.75,
                py: 2,
              }}
            >
              <CircularProgress size={16} />
              <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
                Loading values…
              </Typography>
            </Box>
          )}
          {!isLoading && readMessage && (
            <Box
              role="status"
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 1,
                px: 1.5,
                py: 1,
              }}
            >
              <Typography
                sx={{
                  fontSize: 11,
                  color:
                    readState === "sampled" ? "text.secondary" : "warning.main",
                }}
              >
                {readMessage}
              </Typography>
              {(readState === "error" || readState === "degraded") && (
                <Button
                  size="small"
                  disabled={isRetryingDashboardOptions}
                  onClick={() =>
                    void Promise.resolve(refetchOptions?.()).catch(() => {})
                  }
                  sx={{ minWidth: "auto", fontSize: 11, flexShrink: 0 }}
                >
                  {isRetryingDashboardOptions ? "Retrying…" : "Retry"}
                </Button>
              )}
            </Box>
          )}
          {!isLoading &&
            !isFetchingNextDashboardPage &&
            !readMessage &&
            !search &&
            filtered.length === 0 && (
              <Typography
                sx={{
                  p: 1.5,
                  textAlign: "center",
                  fontSize: 12,
                  color: "text.disabled",
                }}
              >
                {hasMoreDashboardValues
                  ? "No values found yet. Continue searching or enter an exact value."
                  : FREE_TEXT_NO_OPTIONS_TEXT}
              </Typography>
            )}
          {/* Custom-value row is rendered below in the showCustomValueRow
              block — keeps a single source of truth for the "Specify"
              fallback (search did not match any fetched option). */}
          {filtered.map((opt) => {
            const optionValue = getPickerOptionValue(opt);
            const optionType = getPickerOptionType(opt);
            const label = getPickerOptionLabel(opt);
            const secondaryLabel = getPickerOptionSecondaryLabel(opt);
            const isSelected = selectedIndexFor(optionValue, optionType) >= 0;
            return (
              <Box
                key={pickerValueKey(optionValue, optionType)}
                data-filter-value-option={String(optionValue)}
                role={singleSelect ? "radio" : "checkbox"}
                aria-checked={isSelected}
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
                  const customValueType =
                    metricType === "custom_attribute" ? "string" : undefined;
                  if (singleSelect) {
                    onChange([customSearchValue], [customValueType]);
                  } else if (
                    selectedIndexFor(customSearchValue, customValueType) < 0
                  ) {
                    onChange(
                      [...selectedValues, customSearchValue],
                      [...selectedValueTypes, customValueType],
                    );
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
          {!isLoading &&
            !customSearchValueTooLong &&
            dashboardBrowseLimitReached &&
            metricType === "custom_attribute" && (
              <Typography
                role="status"
                sx={{
                  px: 1.5,
                  py: 0.75,
                  fontSize: 11,
                  color: "text.secondary",
                }}
              >
                Recent value limit reached. Search or enter an exact value.
              </Typography>
            )}
          <BoundedCursorPaginationControl
            key={`${projectId || "workspace"}:${filterValueSource}:${property?.registryId || propertyId}:${metricType}:${backendValueSearch}`}
            rootRef={valueOptionsListRef}
            autoAdvanceWhileVisible={false}
            requireUserAdvanceGesture
            channels={[
              {
                channelKey: "values",
                hasNextPage: hasMoreDashboardValues,
                continuationKey: dashboardContinuationKey,
                isFetching: isFetchingNextDashboardPage,
                error: !customSearchValueTooLong && isNextDashboardPageError,
                loadNextPage: loadNextDashboardValues,
              },
            ]}
            loadingLabel="Searching more values…"
            retryLabel="Retry searching values"
            errorMessage="More values could not be loaded. Loaded values remain available."
            testId="filter-value-pagination-sentinel"
          />
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
                onClick={() => onChange([], [])}
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
  allowWorkspaceScope = false,
  onChange,
  onRemove,
  source = "traces",
  ValuePickerOverride,
  categories,
  freeSoloValues = false,
  operatorFilter,
  defaultOperatorForType,
  enableExactAttributeLookup = true,
  unifiedCatalogActive = false,
  isSimulator = false,
  catalogError = false,
  hasNextCatalogPage = false,
  catalogContinuationKey = null,
  isFetchingNextCatalogPage = false,
  catalogNextPageError = false,
  loadNextCatalogPage,
  catalogCategoryCounts,
  catalogCategoryCountsExact,
  attributeSource,
  propertyFilter,
}) {
  const [pickerAnchor, setPickerAnchor] = useState(null);
  const selectedProp = findTraceFilterProperty(properties, filter);
  const normalizedType = normalizeFieldType(filter.fieldType);
  const isNumber = normalizedType === "number";
  const isDate = normalizedType === "date";
  const isBoolean = normalizedType === "boolean";
  const isArray = normalizedType === "array";
  const isMap = normalizedType === "map";
  const allOps = getOperatorsForFilter(filter, selectedProp);
  // Optional per-flow allowlist; currentOpDef resolves against the full set.
  const ops = operatorFilter ? allOps.filter(operatorFilter) : allOps;
  const safeOperator = normalizeFilterRowOperator(filter).operator;
  const currentOpDef = allOps.find((o) => o.value === safeOperator);
  const updateRow = useCallback(
    (changes, options) =>
      onChange(
        index,
        {
          ...filter,
          operator: safeOperator,
          ...changes,
        },
        options,
      ),
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
  const rowHasInvalidMap =
    isMap &&
    !NO_VALUE_OPS.has(safeOperator) &&
    filter.value !== "" &&
    filter.value !== undefined &&
    filter.value !== null &&
    parseMapFilterValue(filter.value) === null;

  const handlePropertySelect = useCallback(
    (prop) => {
      // When the row owns its type, swapping the property must not reset it —
      // the type is the user's choice, not a fact about the field.
      if (prop.typeSelectable && USER_SELECTABLE_TYPES.has(filter.fieldType)) {
        onChange(index, {
          ...filter,
          field: prop.id,
          fieldName: prop.name,
          fieldCategory: prop.category,
          apiColType: prop.apiColType,
        });
        return;
      }
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
      if (nt === "number" || nt === "date" || nt === "map") defaultValue = "";
      else if (nt === "boolean") defaultValue = "true";
      else if (nt === "text") defaultValue = "";
      else defaultValue = [];
      onChange(index, {
        field: prop.id,
        registryId: prop.registryId,
        fieldName: prop.name,
        fieldCategory: prop.category,
        fieldType: nt,
        attributeTypes: prop.attributeTypes,
        attributeTypesExact: prop.attributeTypesExact,
        apiColType: prop.apiColType,
        operator: defaultOp,
        value: defaultValue,
        valueTypes: [],
      });
    },
    [index, filter, onChange, defaultOperatorForType],
  );

  const handleOperatorChange = useCallback(
    (e) => {
      const newOp = e.target.value;
      const opList = getOperatorsForFilter(filter, selectedProp);
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
        !isArray &&
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
      const nextValueTypes = Array.isArray(filter.valueTypes)
        ? filter.valueTypes.slice(
            0,
            Array.isArray(newVal) ? newVal.length : newVal === "" ? 0 : 1,
          )
        : [];
      onChange(index, {
        ...filter,
        operator: newOp,
        value: newVal,
        valueTypes: nextValueTypes,
      });
    },
    [
      index,
      filter,
      selectedProp,
      safeOperator,
      isNumber,
      isDate,
      isArray,
      onChange,
    ],
  );

  const handleTypeChange = useCallback(
    (e) => {
      const fieldType = e.target.value;
      onChange(index, {
        ...filter,
        fieldType,
        operator: DEFAULT_OP_FOR_TYPE[fieldType] || "equals",
        value: fieldType === "boolean" ? "true" : "",
      });
    },
    [index, filter, onChange],
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

    if (isMap) {
      const value = isPlainObject(filter.value)
        ? JSON.stringify(filter.value)
        : filter.value ?? "";
      return (
        <TextField
          size="small"
          type="text"
          placeholder='{"key":"value"}'
          value={value}
          error={rowHasInvalidMap}
          helperText={
            rowHasInvalidMap
              ? "Enter a non-empty flat JSON object with scalar values"
              : undefined
          }
          onChange={(event) => updateRow({ value: event.target.value })}
          sx={{
            flex: "1 1 220px",
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
        valueTypes={filter.valueTypes}
        source={source}
        property={
          selectedProp || {
            id: filter.field,
            registryId: filter.registryId,
            category: filter.fieldCategory,
            apiColType: filter.apiColType,
            type: filter.fieldType,
            attributeTypes: filter.attributeTypes,
            attributeTypesExact: filter.attributeTypesExact,
          }
        }
        freeSoloValues={rowFreeSoloValues}
        singleSelect={SINGLE_VALUE_OPS.has(safeOperator) && !isArray}
        onChange={(newVal, newValueTypes = []) =>
          updateRow(
            { value: newVal, valueTypes: newValueTypes },
            // A picker click is a complete user choice, unlike a partially
            // typed number/map/text editor. Publish it immediately so the
            // request/loading transition does not sit behind the panel's
            // general-purpose typing debounce.
            { commit: true },
          )
        }
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
        mb: rowHasInvalidNumeric || rowHasInvalidMap ? 1.5 : 0,
      }}
    >
      <CustomTooltip
        show={!!(selectedProp?.name || filter.fieldName || filter.field)}
        arrow
        size="small"
        type="black"
        title={selectedProp?.name || filter.fieldName || filter.field || ""}
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
            {selectedProp?.name ||
              filter.fieldName ||
              filter.field ||
              "Property"}
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
        projectId={projectId}
        allowWorkspaceScope={allowWorkspaceScope}
        source={attributeSource || source}
        enableExactAttributeLookup={enableExactAttributeLookup}
        unifiedCatalogActive={unifiedCatalogActive}
        isSimulator={isSimulator}
        catalogError={catalogError}
        hasNextCatalogPage={hasNextCatalogPage}
        catalogContinuationKey={catalogContinuationKey}
        isFetchingNextCatalogPage={isFetchingNextCatalogPage}
        catalogNextPageError={catalogNextPageError}
        loadNextCatalogPage={loadNextCatalogPage}
        catalogCategoryCounts={catalogCategoryCounts}
        catalogCategoryCountsExact={catalogCategoryCountsExact}
        propertyFilter={propertyFilter}
      />

      {selectedProp?.typeSelectable && (
        <Select
          size="small"
          value={filter.fieldType}
          onChange={handleTypeChange}
          sx={{
            flex: "0 1 104px",
            minWidth: 84,
            fontSize: 12,
            height: 28,
          }}
        >
          {FILTER_INPUT_TYPES.map((t) => (
            <MenuItem key={t.value} value={t.value} sx={{ fontSize: 12 }}>
              {t.label}
            </MenuItem>
          ))}
        </Select>
      )}

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
  valueTypes: [],
};

const TraceFilterPanel = ({
  anchorEl,
  open,
  onClose,
  currentFilters,
  onApply,
  filterFields,
  source = "traces",
  propertyNamespace,
  tab = null,
  projectId: projectIdProp,
  allowWorkspaceScope = false,
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
  attributeSource: attributeSourceOverride,
  catalogError: externalCatalogError = false,
  hasNextCatalogPage: externalHasNextCatalogPage = false,
  catalogContinuationKey: externalCatalogContinuationKey = null,
  isFetchingNextCatalogPage: externalIsFetchingNextCatalogPage = false,
  catalogNextPageError: externalCatalogNextPageError = false,
  loadNextCatalogPage: externalLoadNextCatalogPage,
  catalogCategoryCounts: externalCatalogCategoryCounts = null,
  catalogCategoryCountsExact: externalCatalogCategoryCountsExact = false,
}) => {
  const { observeId: routeObserveId } = useParams();
  const observeId = projectIdProp || routeObserveId;
  const skipDynamicProperties = Boolean(propertiesOverride);
  const effectivePropertyNamespace =
    propertyNamespace || defaultPropertyNamespace(tab, source);
  const dynamicPropertySource =
    isSpansView || tab === "spans" ? "spans" : "traces";
  // Voice-call rows are conversation roots, but their custom-property names
  // are still discovered from the span attribute maps.  Keep that key
  // inventory on its own `spans` cursor/cache identity while leaving
  // `source="traces"` untouched below for value discovery and list filters.
  // Conflating the two made a prior trace-key cursor/error appear as the
  // voice picker's result when users repeated the same search.
  const defaultAttributeSource =
    tab === "voiceCalls" ? "spans" : dynamicPropertySource;
  const exactAttributeSource =
    attributeSourceOverride ||
    (source === "traces" ? defaultAttributeSource : source);
  // Voice-call system fields have their own canonical identities, while their
  // eval/annotation/attribute families are derived from the related trace
  // data. The activated catalog bridges those families server-side, so keep
  // one voice_calls source for definitions and counts. The retained-key
  // compatibility cursor below remains on spans.
  const unifiedCatalogSource =
    tab === "voiceCalls" ? "voice_calls" : exactAttributeSource;
  const {
    data: dynamicProperties = [],
    isLoading: dynamicPropsLoading,
    isError: dynamicPropsError,
    hasNextPage: hasNextDynamicPropsPage,
    continuationKey: dynamicPropsContinuationKey,
    fetchNextPage: fetchNextDynamicPropsPage,
    isFetchingNextPage: isFetchingNextDynamicPropsPage,
    isFetchNextPageError: isNextDynamicPropsPageError,
    categoryCounts: dynamicPropertyCategoryCounts,
    categoryCountsExact: dynamicPropertyCategoryCountsExact,
    usesUnifiedCatalog,
  } = useTraceFilterProperties(observeId, {
    // Several pages keep trace/session/voice filter panels mounted at once.
    // Only the visible panel should read the catalog; otherwise one click can
    // fan out into multiple project, source, and attribute requests.
    enabled: !skipDynamicProperties && open,
    isSimulator,
    // Definition pages, category counts, and category continuations must keep
    // one source identity for the lifetime of the picker.
    sourceScope: unifiedCatalogSource,
    allowWorkspaceScope,
  });
  const unifiedPropertyCatalogActive = Boolean(
    !skipDynamicProperties && usesUnifiedCatalog,
  );
  // Merge: static trace fields + dynamic dashboard properties + any extra static fields
  const properties = useMemo(() => {
    if (propertiesOverride) {
      const identifiedProperties = propertiesOverride.map((property) =>
        attachPropertyRegistryIdentity(
          property,
          source,
          effectivePropertyNamespace,
        ),
      );
      return propertyFilter
        ? identifiedProperties.filter(propertyFilter)
        : identifiedProperties;
    }
    const merged = mergeTraceFilterProperties({
      tab,
      isSpansView,
      source,
      propertyNamespace: effectivePropertyNamespace,
      dynamicProperties,
      filterFields,
    });
    return propertyFilter ? merged.filter(propertyFilter) : merged;
  }, [
    dynamicProperties,
    filterFields,
    propertiesOverride,
    propertyFilter,
    effectivePropertyNamespace,
    source,
    tab,
    isSpansView,
  ]);
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
        .map((p) => {
          const type = ["number", "integer", "float"].includes(p.type)
            ? "number"
            : "string";
          return {
            field: p.id,
            property_id: p.registryId,
            label: p.name,
            category: p.category,
            type,
            operators: getOperators(p.type).map((o) => o.value),
            ...(Array.isArray(p.choices) && p.choices.length
              ? { choices: p.choices }
              : {}),
          };
        }),
    [properties],
  );
  const {
    parseQuery: aiParseQuery,
    loading: aiLoading,
    error: aiError,
  } = useAIFilter(aiFilterSchema);
  const [rows, setRows] = useState([{ ...DEFAULT_ROW }]);
  const [queryFieldSearch, setQueryFieldSearch] = useState("");
  const [pinnedQueryAttributeProperties, setPinnedQueryAttributeProperties] =
    useState([]);
  const trimmedQueryFieldSearch = queryFieldSearch.trim();
  const debouncedQueryCatalogSearch = useDebounce(
    trimmedQueryFieldSearch,
    PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS,
  );
  const queryCatalogSearchSettled =
    debouncedQueryCatalogSearch === trimmedQueryFieldSearch;
  const queryCatalogFallbackScope = JSON.stringify([
    "trace-filter-query-property-picker",
    observeId || (allowWorkspaceScope ? "workspace" : ""),
    unifiedCatalogSource,
  ]);
  const queryPropertyCatalog = usePropertyCatalog({
    projectIds: observeId ? [observeId] : [],
    source: unifiedCatalogSource,
    search: debouncedQueryCatalogSearch,
    perEvalConfig: true,
    pageSize: PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
    enabled: Boolean(
      unifiedPropertyCatalogActive &&
        open &&
        showQueryTab &&
        activeTab === "query" &&
        (observeId || allowWorkspaceScope) &&
        debouncedQueryCatalogSearch,
    ),
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: queryCatalogFallbackScope,
  });
  const queryCatalogProperties = useMemo(() => {
    const catalogProperties = buildTraceFilterProperties(
      queryPropertyCatalog.metrics || [],
      {
        isSimulator,
        sourceScope: unifiedCatalogSource,
      },
    );
    return propertyFilter
      ? catalogProperties.filter(propertyFilter)
      : catalogProperties;
  }, [
    isSimulator,
    propertyFilter,
    queryPropertyCatalog.metrics,
    unifiedCatalogSource,
  ]);
  const queryCatalogSearchOwnsResults = Boolean(
    unifiedPropertyCatalogActive &&
      trimmedQueryFieldSearch &&
      queryCatalogSearchSettled &&
      !queryPropertyCatalog.legacyFallbackRequired,
  );
  // Serialized snapshot of the filter set last sent to onApply. Auto-apply
  // compares against this so we only hit the API when the applyable filter set
  // actually changes — picking a field/operator with no value, or re-opening
  // the popover, yields the same set and is skipped.
  const lastAppliedRef = useRef(undefined);

  const queryAttributeLookupEnabled = Boolean(
    !unifiedPropertyCatalogActive &&
      open &&
      showQueryTab &&
      activeTab === "query" &&
      observeId &&
      (exactAttributeSource === "traces" || exactAttributeSource === "spans"),
  );
  const queryLegacyAttributeFallbackActive = Boolean(
    open &&
      showQueryTab &&
      activeTab === "query" &&
      (observeId || allowWorkspaceScope) &&
      (exactAttributeSource === "traces" || exactAttributeSource === "spans") &&
      ((queryPropertyCatalog.legacyFallbackRequired &&
        queryCatalogSearchSettled) ||
        (!unifiedPropertyCatalogActive &&
          (allowWorkspaceScope || Boolean(debouncedQueryCatalogSearch)))),
  );
  const queryLegacyAttributeFallback = useLegacyTraceAttributeProperties(
    observeId,
    {
      enabled: queryLegacyAttributeFallbackActive,
      source: exactAttributeSource,
      search: debouncedQueryCatalogSearch,
      allowWorkspaceScope,
      isSimulator,
      propertyFilter,
    },
  );
  const queryLegacyAttributeFallbackOwnsInventory = Boolean(
    queryLegacyAttributeFallbackActive &&
      (queryLegacyAttributeFallback.isSuccess ||
        queryLegacyAttributeFallback.isError),
  );
  const {
    data: queryExactAttributeProperties = [],
    isFetching: queryAttributeLoading,
    fetchNextPage: fetchNextQueryAttributePage,
    hasNextPage: hasNextQueryAttributePage,
    isFetchingNextPage: isFetchingNextQueryAttributePage,
    isFetchNextPageError: isNextQueryAttributePageError,
    exactSearchError: queryExactAttributeSearchError,
    queryReadState: queryAttributeReadState,
    browseStatus: queryAttributeBrowseStatus,
    debouncedSearch: debouncedQueryAttributeSearch,
  } = useExactTraceAttributeProperties({
    projectId: observeId,
    search: queryFieldSearch,
    source: exactAttributeSource,
    enabled: queryAttributeLookupEnabled,
  });

  const filteredQueryExactAttributeProperties = useMemo(
    () =>
      propertyFilter
        ? queryExactAttributeProperties.filter(propertyFilter)
        : queryExactAttributeProperties,
    [propertyFilter, queryExactAttributeProperties],
  );
  const queryAttributeProperties = useMemo(
    () =>
      mergeRetainedAttributeProperties(
        filteredQueryExactAttributeProperties,
        queryLegacyAttributeFallback.data,
      ),
    [filteredQueryExactAttributeProperties, queryLegacyAttributeFallback.data],
  );
  const queryEffectiveAttributeReadState =
    queryLegacyAttributeFallbackOwnsInventory
      ? queryLegacyAttributeFallback.queryReadState
      : queryAttributeReadState;
  const queryEffectiveAttributeBrowseStatus =
    queryLegacyAttributeFallbackOwnsInventory
      ? queryLegacyAttributeFallback.browseStatus
      : queryAttributeBrowseStatus;
  const queryUsesRetainedAttributePages = shouldUseRetainedAttributePages({
    enabled:
      queryLegacyAttributeFallbackOwnsInventory || queryAttributeLookupEnabled,
    source: exactAttributeSource,
    readState: queryEffectiveAttributeReadState,
    attributes: queryAttributeProperties,
    browseStatus: queryEffectiveAttributeBrowseStatus,
  });
  const selectedQueryAttributeProperties = useMemo(
    () =>
      rows.flatMap((row) => {
        if (
          !row.field ||
          (row.fieldCategory !== "attribute" &&
            row.apiColType !== "SPAN_ATTRIBUTE")
        ) {
          return [];
        }
        return [
          {
            id: row.field,
            registryId:
              row.registryId ||
              row.property_id ||
              `custom_attribute:${row.field}`,
            name: row.fieldName || row.field,
            category: "attribute",
            rawCategory: "custom_attribute",
            type: row.fieldType || "string",
            attributeTypes: row.attributeTypes,
            attributeTypesExact: row.attributeTypesExact,
            apiColType: row.apiColType || "SPAN_ATTRIBUTE",
          },
        ];
      }),
    [rows],
  );
  const queryProperties = useMemo(() => {
    const catalogProperties = queryCatalogSearchOwnsResults
      ? mergeCatalogSearchProperties({
          baseProperties: properties,
          catalogProperties: queryCatalogProperties,
          search: trimmedQueryFieldSearch,
          hasCategorySidebar: false,
        })
      : properties;
    const discovered = mergeRetainedAttributeProperties(
      catalogProperties,
      queryAttributeProperties,
      { canonical: queryUsesRetainedAttributePages },
    );
    const selectedAttributesById = new Map();
    for (const property of [
      ...selectedQueryAttributeProperties,
      ...pinnedQueryAttributeProperties,
    ]) {
      selectedAttributesById.set(property.id, property);
    }
    return mergeRetainedAttributeProperties(discovered, [
      ...selectedAttributesById.values(),
    ]);
  }, [
    pinnedQueryAttributeProperties,
    properties,
    queryCatalogProperties,
    queryCatalogSearchOwnsResults,
    queryAttributeProperties,
    queryUsesRetainedAttributePages,
    selectedQueryAttributeProperties,
    trimmedQueryFieldSearch,
  ]);

  const queryPropertyRegistry = useMemo(
    () => buildQueryPropertyEntries(queryProperties),
    [queryProperties],
  );
  const { entries: queryPropertyEntries } = queryPropertyRegistry;

  // QueryInput needs a unique UI identity for same-id fields. The converter
  // below maps that identity back to the raw backend id before applying.
  const queryFilterFields = useMemo(() => {
    return queryPropertyEntries.map(([identity, p]) => ({
      value: identity,
      label: p.name,
      type: p.type || "string",
      choices: p.choices,
      allowCustomValue:
        p.allowCustomValue === true ||
        (p.category === "annotation" && p.type === "categorical"),
      panelType: p.type || "string",
      category: p.category, // system, eval, annotation, attribute
      rawCategory: p.rawCategory,
      registryId: p.registryId || p.property_id,
      apiColType: p.apiColType,
      attributeTypes: p.attributeTypes,
      attributeTypesExact: p.attributeTypesExact,
    }));
  }, [queryPropertyEntries]);
  const queryFieldMap = useMemo(
    () => Object.fromEntries(queryFilterFields.map((f) => [f.value, f])),
    [queryFilterFields],
  );
  const queryPropertyById = useMemo(
    () => Object.fromEntries(queryPropertyEntries),
    [queryPropertyEntries],
  );
  const queryIdentityForFilter = useCallback(
    (filter) => {
      const property = findTraceFilterProperty(queryProperties, filter);
      return property ? queryPropertyIdentity(property) : filter.field;
    },
    [queryProperties],
  );
  const queryCatalogSearchPageActive = Boolean(
    unifiedPropertyCatalogActive && queryCatalogSearchOwnsResults,
  );
  const queryCatalogSearchPending = Boolean(
    unifiedPropertyCatalogActive &&
      trimmedQueryFieldSearch &&
      !queryCatalogSearchSettled,
  );
  const queryLegacyAttributeFallbackControlsPagination = Boolean(
    queryLegacyAttributeFallbackActive &&
      (queryPropertyCatalog.legacyFallbackRequired ||
        queryLegacyAttributeFallbackOwnsInventory),
  );
  const queryHasNextFieldPage = queryLegacyAttributeFallbackControlsPagination
    ? Boolean(queryLegacyAttributeFallback.hasNextPage)
    : unifiedPropertyCatalogActive
      ? Boolean(
          !queryCatalogSearchPending &&
            (queryCatalogSearchPageActive
              ? queryPropertyCatalog.hasNextPage
              : hasNextDynamicPropsPage),
        )
      : Boolean(hasNextQueryAttributePage);
  const queryIsFetchingNextFieldPage =
    queryLegacyAttributeFallbackControlsPagination
      ? Boolean(queryLegacyAttributeFallback.isFetchingNextPage)
      : unifiedPropertyCatalogActive
        ? queryCatalogSearchPageActive
          ? queryPropertyCatalog.isFetchingNextPage
          : isFetchingNextDynamicPropsPage
        : isFetchingNextQueryAttributePage;
  const queryFieldPageError = queryLegacyAttributeFallbackControlsPagination
    ? Boolean(
        queryLegacyAttributeFallback.isError ||
          queryLegacyAttributeFallback.isFetchNextPageError,
      )
    : unifiedPropertyCatalogActive
      ? queryCatalogSearchPageActive
        ? Boolean(
            queryPropertyCatalog.isError ||
              queryPropertyCatalog.isFetchNextPageError ||
              queryPropertyCatalog.cursorChainStopped,
          )
        : Boolean(dynamicPropsError || isNextDynamicPropsPageError)
      : Boolean(
          isNextQueryAttributePageError || queryExactAttributeSearchError,
        );
  const loadNextQueryFieldPage = useSingleFlightPageRequest({
    identity: JSON.stringify([
      observeId,
      exactAttributeSource,
      queryLegacyAttributeFallbackControlsPagination
        ? "legacy-search"
        : unifiedPropertyCatalogActive
          ? queryCatalogSearchPageActive
            ? "catalog-search"
            : "catalog-base"
          : "retained",
      queryCatalogSearchPageActive
        ? debouncedQueryCatalogSearch
        : debouncedQueryAttributeSearch,
    ]),
    enabled: queryHasNextFieldPage && !queryIsFetchingNextFieldPage,
    request: queryLegacyAttributeFallbackControlsPagination
      ? queryLegacyAttributeFallback.fetchNextPage
      : unifiedPropertyCatalogActive
        ? queryCatalogSearchPageActive
          ? queryPropertyCatalog.fetchNextPage
          : fetchNextDynamicPropsPage
        : fetchNextQueryAttributePage,
  });

  // Query tab — fetch values for the selected field
  const [queryField, setQueryField] = useState(null);
  const [queryValueSearch, setQueryValueSearch] = useState({
    field: null,
    value: "",
  });
  const queryScopeKey = JSON.stringify([
    observeId || (allowWorkspaceScope ? "workspace" : ""),
    source,
    exactAttributeSource,
    unifiedCatalogSource,
  ]);
  const [queryStateScopeKey, setQueryStateScopeKey] = useState(queryScopeKey);
  const queryStateIsCurrent = queryStateScopeKey === queryScopeKey;
  const activeQueryField = queryStateIsCurrent ? queryField : null;
  const activeQueryValueSearch = queryStateIsCurrent
    ? queryValueSearch
    : { field: null, value: "" };
  useEffect(() => {
    setQueryFieldSearch("");
    setPinnedQueryAttributeProperties([]);
    setQueryField(null);
    setQueryValueSearch({ field: null, value: "" });
    setQueryStateScopeKey(queryScopeKey);
  }, [queryScopeKey]);
  const debouncedQueryValueSearch = useDebounce(
    activeQueryValueSearch,
    FILTER_VALUE_SEARCH_DEBOUNCE_MS,
  );
  const queryFieldProp = queryPropertyById[activeQueryField];
  const queryMetricType = (() => {
    const cat = queryFieldProp?.category || "system";
    if (source === "dataset") return "custom_column";
    if (cat === "system") return "system_metric";
    if (cat === "eval") return "eval_metric";
    if (cat === "annotation") return "annotation_metric";
    if (cat === "attribute") return "custom_attribute";
    return "system_metric";
  })();
  const queryValueSource = filterValueTransportSource(
    source === "dataset" ? "dataset_column" : source,
    queryMetricType,
  );
  const queryValueMetricName = filterValueAdapterMetricName(
    queryValueSource,
    queryFieldProp?.id || activeQueryField || "",
  );
  const isQuerySessionFreeTextField =
    queryValueSource === "sessions" &&
    SESSION_FREE_TEXT_FIELDS.has(queryFieldProp?.id || activeQueryField || "");
  const queryValuePropertyId = buildPropertyRegistryId({
    propertyId: queryFieldProp?.registryId,
    metricName: queryValueMetricName,
    metricType: queryMetricType,
    source: queryValueSource,
  });
  const shouldFetchQueryValues = Boolean(
    open &&
      activeTab === "query" &&
      activeQueryField &&
      !isQuerySessionFreeTextField &&
      (!queryFieldProp?.choices?.length ||
        (queryFieldProp?.category === "annotation" &&
          queryFieldProp?.type === "categorical")),
  );
  const effectiveQueryValueSearch =
    debouncedQueryValueSearch?.field === activeQueryField
      ? debouncedQueryValueSearch.value
      : "";
  const effectiveQueryValueSearchGesture =
    activeQueryValueSearch?.field === activeQueryField
      ? activeQueryValueSearch.value
      : "";
  const {
    data: queryValueOptions = [],
    isLoading: queryValuesLoading,
    isError: queryValuesError,
    queryReadState: queryValuesReadState,
    fetchNextPage: fetchNextQueryValuesPage,
    hasNextPage: hasNextQueryValuesPage,
    isFetchingNextPage: isFetchingNextQueryValuesPage,
    isFetchNextPageError: isNextQueryValuesPageError,
    cursorChainStopped: queryValueCursorStopped,
    retryFreshPage: retryQueryValues,
    isRetryingFreshPage: isRetryingQueryValues,
    refetch: refetchQueryValues,
  } = useDashboardFilterValues({
    propertyId: queryValuePropertyId,
    metricName: queryValueMetricName,
    metricType: queryMetricType,
    projectIds: source === "dataset" ? [] : observeId ? [observeId] : [],
    datasetId: source === "dataset" ? observeId : undefined,
    source: queryValueSource,
    search: [
      "custom_attribute",
      "system_metric",
      "annotation_metric",
      "eval_metric",
    ].includes(queryMetricType)
      ? effectiveQueryValueSearch
      : "",
    searchGesture: [
      "custom_attribute",
      "system_metric",
      "annotation_metric",
      "eval_metric",
    ].includes(queryMetricType)
      ? effectiveQueryValueSearchGesture
      : "",
    // System, eval, and annotation values must use the same signed-cursor
    // contract as custom attributes. A missing page_size enters the legacy
    // non-pageable branch and can exceed the property picker's five-second SLA.
    pageSize: FILTER_VALUE_PAGE_SIZE,
    attributeType:
      queryMetricType === "custom_attribute"
        ? queryFieldProp?.attributeTypesExact === true &&
          queryFieldProp?.attributeTypes?.length === 1
          ? queryFieldProp?.type === "text"
            ? "string"
            : ["float", "integer"].includes(queryFieldProp?.type)
              ? "number"
              : queryFieldProp?.type
          : undefined
        : undefined,
    enabled: shouldFetchQueryValues,
  });
  const loadNextQueryValuesPage = useSingleFlightPageRequest({
    identity: JSON.stringify([
      observeId,
      source,
      activeQueryField,
      effectiveQueryValueSearch,
    ]),
    enabled:
      Boolean(
        hasNextQueryValuesPage || queryValuesError || queryValueCursorStopped,
      ) &&
      !isFetchingNextQueryValuesPage &&
      !isRetryingQueryValues,
    request: () =>
      hasNextQueryValuesPage
        ? fetchNextQueryValuesPage()
        : (retryQueryValues || refetchQueryValues)?.(),
  });
  const queryValuesMessage = getFilterValueReadMessage(
    queryValuesError || isNextQueryValuesPageError
      ? "error"
      : queryValuesReadState,
  );

  useEffect(() => {
    if (open) {
      if (currentFilters?.length) {
        // Enrich rows with fieldCategory and fieldType from properties lookup
        const enriched = currentFilters.map((f) => {
          const prop = findTraceFilterProperty(properties, f);
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
            registryId: f.registryId || f.property_id || prop?.registryId,
            fieldCategory: resolveFieldCategory(f.fieldCategory, prop),
            fieldName: f.fieldName || prop?.name,
            fieldType,
            attributeTypes: f.attributeTypes || prop?.attributeTypes,
            attributeTypesExact:
              f.attributeTypesExact ?? prop?.attributeTypesExact,
            apiColType: f.apiColType || prop?.apiColType,
            operator: hydratedOp,
            value,
            valueTypes: f.valueTypes,
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
        const prop = queryPropertyById[t.field];
        const fieldType =
          prop?.type ||
          queryFieldDef?.panelType ||
          (queryFieldDef?.type === "enum" ? "categorical" : "string");
        const value = NO_VALUE_OPS.has(t.operator)
          ? ""
          : normalizeFieldType(fieldType) === "map"
            ? t.value
            : Array.isArray(t.value)
              ? t.value
              : [t.value];
        return {
          field: prop?.id || t.field,
          registryId:
            prop?.registryId ||
            prop?.property_id ||
            queryFieldDef?.registryId ||
            queryFieldDef?.property_id,
          fieldName: prop?.name || queryFieldDef?.label,
          fieldCategory: resolveFieldCategory(undefined, prop || queryFieldDef),
          fieldType,
          attributeTypes: prop?.attributeTypes || queryFieldDef?.attributeTypes,
          attributeTypesExact:
            prop?.attributeTypesExact ?? queryFieldDef?.attributeTypesExact,
          apiColType: prop?.apiColType || queryFieldDef?.apiColType,
          operator: QUERY_TO_BASIC_OP[t.operator] || t.operator,
          valueTypes: t.valueTypes,
          value,
        };
      }),
    [queryFieldMap, queryPropertyById],
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
      const ops = getOperatorsForFilter({
        field: queryPropertyById[field]?.id || field,
        fieldType: type,
      });
      const allowed = operatorFilter ? ops.filter(operatorFilter) : ops;
      return allowed.map((op) =>
        NO_VALUE_OPS.has(op.value) ? { ...op, noValue: true } : op,
      );
    },
    [operatorFilter, queryPropertyById],
  );

  // Auto-apply: filters take effect as soon as a value is chosen (debounced),
  // so there's no Apply button — only Clear all. We apply WITHOUT closing the
  // popover so the user can keep adjusting filters and see them apply live.
  const autoApplyTimerRef = useRef(null);
  // ObserveToolbar adapts this callback inline. Keep the latest implementation
  // without making its changing identity restart the debounce; graph/query
  // progress renders can otherwise postpone a committed filter indefinitely.
  const onApplyRef = useRef(onApply);
  onApplyRef.current = onApply;
  // Apply only when the resulting filter set differs from the last one sent.
  const applyIfChanged = useCallback((sourceRows) => {
    // Hold while a typed editor is incomplete so partial values do not
    // auto-fire or drop the last valid, already-applied filter.
    if (hasIncompleteNumericRow(sourceRows) || hasIncompleteMapRow(sourceRows))
      return;
    const next = computeValidFilters(sourceRows);
    const serialized = serializeFilterSet(next);
    if (serialized === lastAppliedRef.current) return;
    lastAppliedRef.current = serialized;
    onApplyRef.current(next);
  }, []);

  const handleChange = useCallback(
    (idx, updated, { commit = false } = {}) => {
      const nextRows = rows.map((r, i) => (i === idx ? updated : r));
      setRows(nextRows);
      if (!commit) return;
      if (autoApplyTimerRef.current) {
        clearTimeout(autoApplyTimerRef.current);
        autoApplyTimerRef.current = null;
      }
      applyIfChanged(nextRows);
    },
    [applyIfChanged, rows],
  );

  const handleRemove = useCallback(
    (idx) => {
      setRows((prev) => {
        const next = prev.filter((_, i) => i !== idx);
        return next.length ? next : [{ ...effectiveDefaultRow }];
      });
    },
    [effectiveDefaultRow],
  );

  useEffect(() => {
    if (!open) return undefined;
    if (autoApplyTimerRef.current) clearTimeout(autoApplyTimerRef.current);
    autoApplyTimerRef.current = setTimeout(
      () => applyIfChanged(rows),
      FILTER_AUTO_APPLY_DEBOUNCE_MS,
    );
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
    let aiFilters;
    try {
      aiFilters = await aiParseQuery(aiQuery, {
        smart: true,
        projectId: observeId,
        source,
      });
    } catch {
      return;
    }
    if (aiFilters.length > 0) {
      const aiRows = aiFilters.map((f) => {
        // Attribute fields are intentionally excluded from aiFilterSchema;
        // keep same-id raw attributes from hijacking an AI-selected system id.
        const prop = f.property_id
          ? properties.find(
              (property) =>
                property.registryId === f.property_id &&
                property.category !== "attribute",
            )
          : properties.find(
              (property) =>
                property.id === f.field && property.category !== "attribute",
            );
        const fieldType = prop?.type || "string";
        return {
          field: prop?.id || f.field,
          registryId: f.property_id || prop?.registryId,
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
            <Stack spacing={1}>
              {propsLoading && (
                <Box
                  role="status"
                  sx={{ display: "flex", alignItems: "center", gap: 0.75 }}
                >
                  <CircularProgress size={14} />
                  <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
                    Loading properties…
                  </Typography>
                </Box>
              )}
              {rows.map((row, idx) => (
                <FilterRow
                  key={idx}
                  filter={row}
                  index={idx}
                  properties={properties}
                  projectId={observeId}
                  allowWorkspaceScope={allowWorkspaceScope}
                  onChange={handleChange}
                  onRemove={handleRemove}
                  source={source}
                  ValuePickerOverride={ValuePickerOverride}
                  categories={effectiveCategories}
                  freeSoloValues={freeSoloValues}
                  operatorFilter={operatorFilter}
                  defaultOperatorForType={defaultOperatorForType}
                  enableExactAttributeLookup={Boolean(
                    !unifiedPropertyCatalogActive &&
                      observeId &&
                      (exactAttributeSource === "traces" ||
                        exactAttributeSource === "spans"),
                  )}
                  unifiedCatalogActive={unifiedPropertyCatalogActive}
                  isSimulator={isSimulator}
                  catalogError={
                    skipDynamicProperties
                      ? externalCatalogError
                      : dynamicPropsError
                  }
                  hasNextCatalogPage={
                    skipDynamicProperties
                      ? externalHasNextCatalogPage
                      : hasNextDynamicPropsPage
                  }
                  catalogContinuationKey={
                    skipDynamicProperties
                      ? externalCatalogContinuationKey
                      : dynamicPropsContinuationKey
                  }
                  isFetchingNextCatalogPage={
                    skipDynamicProperties
                      ? externalIsFetchingNextCatalogPage
                      : isFetchingNextDynamicPropsPage
                  }
                  catalogNextPageError={
                    skipDynamicProperties
                      ? externalCatalogNextPageError
                      : isNextDynamicPropsPageError
                  }
                  loadNextCatalogPage={
                    skipDynamicProperties
                      ? externalLoadNextCatalogPage
                      : fetchNextDynamicPropsPage
                  }
                  catalogCategoryCounts={
                    skipDynamicProperties
                      ? externalCatalogCategoryCounts
                      : dynamicPropertyCategoryCounts
                  }
                  catalogCategoryCountsExact={
                    skipDynamicProperties
                      ? externalCatalogCategoryCountsExact
                      : dynamicPropertyCategoryCountsExact
                  }
                  attributeSource={
                    unifiedPropertyCatalogActive
                      ? unifiedCatalogSource
                      : exactAttributeSource
                  }
                  propertyFilter={propertyFilter}
                />
              ))}
            </Stack>
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
              key={queryScopeKey}
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
                .map((r) => {
                  const normalizedRow = normalizeFilterRowOperator(r);
                  const value =
                    normalizeFieldType(r.fieldType) === "map" &&
                    isPlainObject(r.value)
                      ? JSON.stringify(r.value)
                      : Array.isArray(r.value)
                        ? r.value
                        : r.value ?? "";
                  return {
                    field: queryIdentityForFilter(r),
                    operator:
                      BASIC_TO_QUERY_OP[normalizedRow.operator] ||
                      normalizedRow.operator,
                    value,
                    valueTypes: r.valueTypes,
                  };
                })}
              valueOptions={queryValueOptions}
              fieldLoading={
                queryLegacyAttributeFallbackControlsPagination
                  ? Boolean(
                      queryLegacyAttributeFallback.isLoading &&
                        !queryLegacyAttributeFallback.isFetchingNextPage,
                    )
                  : unifiedPropertyCatalogActive
                    ? Boolean(
                        trimmedQueryFieldSearch &&
                          (!queryCatalogSearchSettled ||
                            (queryPropertyCatalog.isLoading &&
                              !queryPropertyCatalog.isFetchingNextPage)),
                      )
                    : queryAttributeLookupEnabled &&
                      ((queryAttributeLoading &&
                        !isFetchingNextQueryAttributePage) ||
                        trimmedQueryFieldSearch !==
                          debouncedQueryAttributeSearch)
              }
              fieldLoadingMore={queryIsFetchingNextFieldPage}
              fieldLoadError={queryFieldPageError}
              hasMoreFields={queryHasNextFieldPage}
              onLoadMoreFields={loadNextQueryFieldPage}
              onFieldSearchChange={setQueryFieldSearch}
              valueLoading={queryValuesLoading}
              valueLoadingMore={
                isFetchingNextQueryValuesPage || isRetryingQueryValues
              }
              valueLoadError={Boolean(
                queryValuesError ||
                  isNextQueryValuesPageError ||
                  queryValueCursorStopped,
              )}
              hasMoreValues={Boolean(
                hasNextQueryValuesPage ||
                  queryValuesError ||
                  queryValueCursorStopped,
              )}
              onLoadMoreValues={loadNextQueryValuesPage}
              onValueSearchChange={(value, field) =>
                setQueryValueSearch({
                  field: field ?? activeQueryField,
                  value,
                })
              }
              onFieldChange={(field) => {
                setQueryValueSearch({ field, value: "" });
                setQueryField(field);
                setQueryFieldSearch("");
                const selectedProperty = queryPropertyById[field];
                if (selectedProperty?.category === "attribute") {
                  setPinnedQueryAttributeProperties((current) =>
                    mergeRetainedAttributeProperties(current, [
                      selectedProperty,
                    ]),
                  );
                }
              }}
            />
            {shouldFetchQueryValues && queryValuesMessage && (
              <Typography
                role="status"
                sx={{ fontSize: 11, color: "warning.main", mt: 0.75, px: 0.5 }}
              >
                {queryValuesMessage}
              </Typography>
            )}
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
  propertyNamespace: PropTypes.string,
  tab: PropTypes.oneOf(["trace", "spans", "voiceCalls"]),
  projectId: PropTypes.string,
  allowWorkspaceScope: PropTypes.bool,
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
  attributeSource: PropTypes.oneOf(["traces", "spans"]),
  catalogError: PropTypes.bool,
  hasNextCatalogPage: PropTypes.bool,
  catalogContinuationKey: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.number,
  ]),
  isFetchingNextCatalogPage: PropTypes.bool,
  catalogNextPageError: PropTypes.bool,
  loadNextCatalogPage: PropTypes.func,
  catalogCategoryCounts: PropTypes.object,
  catalogCategoryCountsExact: PropTypes.bool,
};

export default React.memo(TraceFilterPanel);
