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
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import axios, { endpoints } from "src/utils/axios";
import { useDashboardFilterValues } from "src/hooks/useDashboards";
import { useAIFilter } from "src/hooks/use-ai-filter";
import { QueryInput } from "src/components/filter-panel";

// ---------------------------------------------------------------------------
// Trace filter fields (for Query tab via shared FilterPanel)
// ---------------------------------------------------------------------------
const TRACE_FILTER_FIELDS = [
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
  { value: "user_id", label: "User ID", type: "string" },
  { value: "service_name", label: "Service / Trace Name", type: "string" },
  { value: "provider", label: "Provider", type: "string" },
  { value: "span_kind", label: "Span Kind", type: "string" },
  { value: "tag", label: "Tag", type: "string" },
];

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

const STRING_OPS = [
  { value: "is", label: "is" },
  { value: "is_not", label: "is not" },
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "not contains" },
  { value: "starts_with", label: "starts with" },
];

const NUMBER_OPS = [
  { value: "equal_to", label: "equals" },
  { value: "not_equal_to", label: "not equal" },
  { value: "greater_than", label: "greater than" },
  { value: "greater_than_or_equal", label: "greater than or equals" },
  { value: "less_than", label: "less than" },
  { value: "less_than_or_equal", label: "less than or equals" },
  { value: "between", label: "between", range: true },
  { value: "not_between", label: "not between", range: true },
];

const DATE_OPS = [
  { value: "before", label: "before" },
  { value: "after", label: "after" },
  { value: "on", label: "on" },
  { value: "between", label: "between", range: true },
  { value: "not_between", label: "not between", range: true },
];

const BOOLEAN_OPS = [{ value: "is", label: "is" }];

const ARRAY_OPS = [
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "not contains" },
  { value: "is_empty", label: "is empty" },
  { value: "is_not_empty", label: "is not empty" },
];

const CATEGORICAL_OPS = [
  { value: "is", label: "is" },
  { value: "is_not", label: "is not" },
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "not contains" },
];

const TEXT_OPS = [
  { value: "is", label: "is" },
  { value: "is_not", label: "is not" },
  { value: "contains", label: "contains" },
  { value: "not_contains", label: "not contains" },
  { value: "starts_with", label: "starts with" },
  { value: "ends_with", label: "ends with" },
];

// Map QueryInput operator keys → Basic tab operator keys
const QUERY_TO_BASIC_OP = {
  equals: "is",
  not_equals: "is_not",
  starts_with: "starts_with",
};

// Reverse: Basic tab operator keys → QueryInput operator keys
const BASIC_TO_QUERY_OP = {
  is: "equals",
  is_not: "not_equals",
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

const getOperators = (fieldType) => {
  if (fieldType === "categorical") return CATEGORICAL_OPS;
  if (fieldType === "text") return TEXT_OPS;
  const t = normalizeFieldType(fieldType);
  if (t === "number") return NUMBER_OPS;
  if (t === "date") return DATE_OPS;
  if (t === "boolean") return BOOLEAN_OPS;
  if (t === "array") return ARRAY_OPS;
  return STRING_OPS;
};

const DEFAULT_OP_FOR_TYPE = {
  number: "equal_to",
  date: "on",
  boolean: "is",
  array: "contains",
  string: "is",
  categorical: "is",
  text: "is",
};

const NO_VALUE_OPS = new Set([
  "is_empty",
  "is_not_empty",
  "is_null",
  "is_not_null",
]);

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
]);

function useTraceFilterProperties(
  projectId,
  { enabled = true, isSimulator = false } = {},
) {
  return useQuery({
    queryKey: ["trace-filter-properties-v2", projectId],
    enabled,
    queryFn: async () => {
      const params = {};
      if (projectId) params.project_ids = projectId;
      const { data } = await axios.get(endpoints.dashboard.metrics, { params });
      return data?.result?.metrics || [];
    },
    select: (metrics) =>
      metrics
        .filter((m) => {
          const name = m.name;
          const cat = m.category;
          const src = m.source;

          // Always exclude blacklisted metrics
          if (EXCLUDED_METRICS.has(name)) return false;

          // Exclude dataset-only metrics
          if (src === "datasets") return false;

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
          if (cat === "custom_attribute" || cat === "customAttribute")
            return true;

          return false;
        })
        .map((m) => {
          const outputType = m.outputType || m.output_type;
          // Eval metrics don't carry a `type` field; derive the filter
          // input type from `output_type`. SCORE → number (slider),
          // PASS_FAIL/CHOICE/CHOICES → string (dropdown of choices).
          const isEval =
            m.category === "eval_metric" || m.category === "evalMetric";
          const isAnnotation =
            m.category === "annotation_metric" ||
            m.category === "annotationMetric";
          let type;
          if (isEval && outputType) {
            const ot = String(outputType).toUpperCase();
            if (ot === "SCORE") type = "number";
            else type = "string";
          } else if (isAnnotation && outputType) {
            const ot = String(outputType).toLowerCase();
            if (ot === "numeric" || ot === "star") type = "number";
            else if (ot === "text") type = "text";
            else type = "categorical"; // categorical, thumbs_up_down
          } else {
            type = normalizeFieldType(m.type);
          }
          return {
            id: m.name,
            name: m.displayName || m.display_name || m.name,
            category: mapCategory(m.category),
            rawCategory: m.category,
            type,
            outputType,
            choices: m.choices,
          };
        }),
    staleTime: 60_000,
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

  const filtered = useMemo(() => {
    let list = properties;
    if (hasCategorySidebar && category !== "all")
      list = list.filter((p) => p.category === category);
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (p) =>
          p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q),
      );
    }
    return list;
  }, [properties, category, search, hasCategorySidebar]);

  const counts = useMemo(() => {
    const c = { all: properties.length };
    for (const p of properties) c[p.category] = (c[p.category] || 0) + 1;
    return c;
  }, [properties]);

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
              {filtered.map((prop) => (
                <Box
                  key={prop.id}
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

function getPickerOptionValue(option) {
  if (typeof option === "string") return option;
  return option?.value ?? option?.label ?? "";
}

function getPickerOptionLabel(option) {
  if (typeof option === "string") return option;
  return option?.label ?? option?.value ?? "";
}

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
}) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");
  const debouncedSearch = search; // could add debounce for large datasets

  // If the property declares its own static choices (e.g. the Project filter
  // on the cross-project user-detail page), use them directly. Skips both
  // the dashboard lookup and the session fallback — useful when the field is
  // not indexed by the dashboard metrics pipeline or when options are known
  // client-side.
  const hasStaticChoices =
    Array.isArray(property?.choices) && property.choices.length > 0;

  const metricType = (() => {
    if (propertyCategory === "system") return "system_metric";
    if (propertyCategory === "eval") return "eval_metric";
    if (propertyCategory === "annotation") return "annotation_metric";
    if (propertyCategory === "attribute") return "custom_attribute";
    return "system_metric";
  })();

  const isSessionField =
    !hasStaticChoices && SESSION_VALUE_FIELDS.has(propertyId);

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
    enabled: !hasStaticChoices,
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
    if (!search || isSessionField) return options; // session endpoint already filters server-side
    const q = search.toLowerCase();
    return options.filter((o) => {
      const label = getPickerOptionLabel(o);
      return label.toLowerCase().includes(q);
    });
  }, [options, search, isSessionField]);

  const selectedValues = useMemo(() => normalizePickerValues(value), [value]);

  const toggleValue = useCallback(
    (val) => {
      const strVal = getPickerOptionValue(val);
      onChange(
        selectedValues.includes(strVal)
          ? selectedValues.filter((v) => v !== strVal)
          : [...selectedValues, strVal],
      );
    },
    [selectedValues, onChange],
  );

  const customSearchValue = search.trim();
  const searchMatchesExistingOption = options.some(
    (option) =>
      getPickerOptionValue(option).toLowerCase() ===
      customSearchValue.toLowerCase(),
  );
  const showCustomValueRow = Boolean(
    customSearchValue && !searchMatchesExistingOption,
  );

  return (
    <>
      <Box
        onClick={(e) => setAnchorEl(e.currentTarget)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          flexWrap: "wrap",
          minHeight: 28,
          minWidth: 120,
          flex: 1,
          maxWidth: 250,
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
                ? "Select values..."
                : "Select values..."}
          </Typography>
        ) : (
          selectedValues.slice(0, 3).map((v) => {
            // Resolve the display label from static choices or rendered
            // options. Falls back to the raw value (e.g. plain strings
            // without a label).
            const match = options.find((o) => {
              const ov = typeof o === "string" ? o : o.value;
              return ov === v;
            });
            const displayLabel =
              (typeof match === "string" ? match : match?.label) || v;
            return (
              <Chip
                key={v}
                label={displayLabel}
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
        {selectedValues.length > 3 && (
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
          paper: { sx: { width: 260, borderRadius: "8px", mt: 0.5 } },
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
            Select one or more values (multi-select)
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
          {filtered.map((opt) => {
            const strVal = getPickerOptionValue(opt);
            const label = getPickerOptionLabel(opt);
            const isSelected = selectedValues.includes(strVal);
            return (
              <Box
                key={strVal}
                onClick={() => toggleValue(opt)}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  px: 1.5,
                  py: 0.75,
                  cursor: "pointer",
                  bgcolor: isSelected ? "action.selected" : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify
                  icon={
                    isSelected
                      ? "mdi:checkbox-marked"
                      : "mdi:checkbox-blank-outline"
                  }
                  width={18}
                  sx={{
                    color: isSelected ? "primary.main" : "text.secondary",
                    flexShrink: 0,
                  }}
                />
                <Typography
                  noWrap
                  sx={{
                    fontSize: 12,
                    flex: 1,
                    maxWidth: 180,
                    fontWeight: isSelected ? 600 : 400,
                    color: isSelected ? "text.primary" : "text.primary",
                  }}
                >
                  {label}
                </Typography>
              </Box>
            );
          })}
          {showCustomValueRow && (
            <>
              {filtered.length > 0 && <Divider />}
              <Box
                onClick={() => {
                  if (!selectedValues.includes(customSearchValue)) {
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
}) {
  const [pickerAnchor, setPickerAnchor] = useState(null);
  const selectedProp = properties.find((p) => p.id === filter.field);
  const normalizedType = normalizeFieldType(filter.fieldType);
  const isNumber = normalizedType === "number";
  const isDate = normalizedType === "date";
  const isBoolean = normalizedType === "boolean";
  const ops = getOperators(filter.fieldType);
  const currentOpDef = ops.find((o) => o.value === filter.operator);
  const rowFreeSoloValues =
    typeof freeSoloValues === "function"
      ? freeSoloValues(filter)
      : freeSoloValues;

  const handlePropertySelect = useCallback(
    (prop) => {
      // Preserve custom annotation types (categorical, text) — normalizeFieldType
      // would collapse them to "string" losing operator/input specificity.
      const nt =
        prop.type === "categorical" || prop.type === "text"
          ? prop.type
          : normalizeFieldType(prop.type);
      const defaultOp = DEFAULT_OP_FOR_TYPE[nt] || "is";
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
        operator: defaultOp,
        value: defaultValue,
      });
    },
    [index, onChange],
  );

  const handleOperatorChange = useCallback(
    (e) => {
      const newOp = e.target.value;
      const opList = getOperators(filter.fieldType);
      const newDef = opList.find((o) => o.value === newOp);
      const oldDef = opList.find((o) => o.value === filter.operator);
      let newVal = filter.value;
      if (isNumber || isDate) {
        if (newDef?.range && !oldDef?.range) newVal = ["", ""];
        else if (!newDef?.range && oldDef?.range) newVal = "";
      }
      if (NO_VALUE_OPS.has(newOp)) newVal = "";
      onChange(index, { ...filter, operator: newOp, value: newVal });
    },
    [index, filter, isNumber, isDate, onChange],
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

    if (NO_VALUE_OPS.has(filter.operator)) {
      return <Box sx={{ flex: 1 }} />;
    }

    if (isBoolean) {
      return (
        <Select
          size="small"
          value={filter.value ?? "true"}
          onChange={(e) =>
            onChange(index, { ...filter, value: e.target.value })
          }
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
            sx={{ flex: 1, minWidth: 180, maxWidth: 280 }}
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
                onChange(index, { ...filter, value: cur });
              }}
              sx={{ flex: 1 }}
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
                onChange(index, { ...filter, value: cur });
              }}
              sx={{ flex: 1 }}
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
          onChange={(e) =>
            onChange(index, { ...filter, value: e.target.value })
          }
          sx={{ flex: 1, minWidth: 140, maxWidth: 200 }}
          inputProps={{
            style: { fontSize: 11, height: 12, padding: "6px 6px" },
          }}
        />
      );
    }

    if (isNumber) {
      if (currentOpDef?.range) {
        return (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ flex: 1, minWidth: 120, maxWidth: 200 }}
          >
            <TextField
              size="small"
              type="number"
              placeholder="Min"
              value={Array.isArray(filter.value) ? filter.value[0] ?? "" : ""}
              onChange={(e) => {
                const cur = Array.isArray(filter.value)
                  ? [...filter.value]
                  : ["", ""];
                cur[0] = e.target.value;
                onChange(index, { ...filter, value: cur });
              }}
              sx={{ flex: 1 }}
              inputProps={{
                style: { fontSize: 12, height: 12, padding: "6px 8px" },
              }}
            />
            <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
              and
            </Typography>
            <TextField
              size="small"
              type="number"
              placeholder="Max"
              value={Array.isArray(filter.value) ? filter.value[1] ?? "" : ""}
              onChange={(e) => {
                const cur = Array.isArray(filter.value)
                  ? [...filter.value]
                  : ["", ""];
                cur[1] = e.target.value;
                onChange(index, { ...filter, value: cur });
              }}
              sx={{ flex: 1 }}
              inputProps={{
                style: { fontSize: 12, height: 12, padding: "6px 8px" },
              }}
            />
          </Stack>
        );
      }
      return (
        <TextField
          size="small"
          type="number"
          placeholder="Value"
          value={filter.value ?? ""}
          onChange={(e) =>
            onChange(index, { ...filter, value: e.target.value })
          }
          sx={{ flex: 1, minWidth: 80, maxWidth: 140 }}
          inputProps={{
            style: { fontSize: 12, height: 12, padding: "6px 8px" },
          }}
        />
      );
    }

    if (filter.fieldType === "text") {
      return (
        <TextField
          size="small"
          placeholder="Enter text..."
          value={filter.value ?? ""}
          onChange={(e) =>
            onChange(index, { ...filter, value: e.target.value })
          }
          sx={{ flex: 1, minWidth: 120, maxWidth: 200 }}
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
        onChange={(newVal) => onChange(index, { ...filter, value: newVal })}
      />
    );
  };

  return (
    <Stack direction="row" alignItems="center" gap={0.5}>
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
            minWidth: 100,
            maxWidth: 150,
            borderColor: "divider",
            color: filter.field ? "text.primary" : "text.disabled",
            justifyContent: "space-between",
          }}
        >
          <Typography
            noWrap
            sx={{
              fontSize: 12,
              maxWidth: 100,
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
        value={filter.operator || (isNumber ? "equal_to" : "is")}
        onChange={handleOperatorChange}
        sx={{ minWidth: 70, fontSize: 12, height: 28 }}
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
        sx={{ p: 0.25, flexShrink: 0 }}
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
  operator: "is",
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
  projectId: projectIdProp,
  properties: propertiesOverride,
  ValuePickerOverride,
  showAi = true,
  showQueryTab = true,
  categories: categoriesOverride,
  panelWidth,
  defaultRow: defaultRowOverride,
  isSimulator = false,
  freeSoloValues = false,
}) => {
  const { observeId: routeObserveId } = useParams();
  const observeId = projectIdProp || routeObserveId;
  const skipDynamicProperties = Boolean(propertiesOverride);
  const { data: dynamicProperties = [], isLoading: dynamicPropsLoading } =
    useTraceFilterProperties(observeId, {
      enabled: !skipDynamicProperties,
      isSimulator,
    });
  // Merge: static trace fields + dynamic dashboard properties + any extra static fields
  const properties = useMemo(() => {
    if (propertiesOverride) return propertiesOverride;
    // Start with static trace fields (trace_name, status, model, etc.)
    const staticProps = TRACE_FILTER_FIELDS.map((f) => ({
      id: f.value,
      name: f.label,
      category: "system",
      type: f.type === "enum" ? "string" : f.type,
      ...(f.choices ? { choices: f.choices } : {}),
    }));
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
    return [...staticProps, ...dynamicExtras, ...fieldExtras];
  }, [dynamicProperties, filterFields, propertiesOverride]);
  const propertyById = useMemo(
    () => Object.fromEntries(properties.map((p) => [p.id, p])),
    [properties],
  );
  const propsLoading = skipDynamicProperties ? false : dynamicPropsLoading;
  const effectiveCategories = categoriesOverride ?? CATEGORIES;
  const effectiveDefaultRow = defaultRowOverride || DEFAULT_ROW;
  const [activeTab, setActiveTab] = useState("basic");
  const [aiQuery, setAiQuery] = useState("");
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

  // Convert dashboard properties to QueryInput format (same IDs as dashboard API)
  const queryFilterFields = useMemo(
    () =>
      properties.map((p) => ({
        value: p.id,
        label: p.name,
        type: p.choices?.length ? "enum" : "string",
        choices: p.choices,
        panelType: p.type || "string",
        category: p.category, // system, eval, annotation, attribute
      })),
    [properties],
  );
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
          return {
            ...f,
            fieldCategory: f.fieldCategory || prop?.category || "system",
            fieldName: f.fieldName || prop?.name,
            fieldType: f.fieldType || prop?.type || "string",
          };
        });
        setRows(enriched);
      } else {
        setRows([{ ...effectiveDefaultRow }]);
      }
    }
  }, [open, currentFilters]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleQueryTokensChange = useCallback(
    (tokens) => {
      const converted = tokens.map((t) => {
        const queryFieldDef = queryFieldMap[t.field];
        const prop = propertyById[t.field];
        return {
          field: t.field,
          fieldName: prop?.name || queryFieldDef?.label,
          fieldCategory: prop?.category || queryFieldDef?.category || "system",
          fieldType:
            prop?.type ||
            queryFieldDef?.panelType ||
            (queryFieldDef?.type === "enum" ? "categorical" : "string"),
          operator: QUERY_TO_BASIC_OP[t.operator] || t.operator,
          value: Array.isArray(t.value) ? t.value : [t.value],
        };
      });
      setRows(converted.length ? converted : [{ ...effectiveDefaultRow }]);
    },
    [effectiveDefaultRow, propertyById, queryFieldMap],
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

  const handleApply = useCallback(() => {
    const valid = rows.filter((r) => {
      if (!r.field) return false;
      if (NO_VALUE_OPS.has(r.operator)) return true;
      const ops = getOperators(r.fieldType);
      const opDef = ops.find((o) => o.value === r.operator);
      if (opDef?.range)
        return Array.isArray(r.value) && r.value[0] !== "" && r.value[1] !== "";
      if (Array.isArray(r.value)) return r.value.length > 0;
      return r.value !== "" && r.value !== undefined && r.value !== null;
    });
    onApply(valid.length ? valid : null);
    onClose();
  }, [rows, onApply, onClose]);

  const handleClear = useCallback(() => {
    setRows([{ ...effectiveDefaultRow }]);
    onApply(null);
    onClose();
  }, [onApply, onClose, effectiveDefaultRow]);

  const handleAiFilter = useCallback(async () => {
    if (!aiQuery.trim()) return;
    const aiFilters = await aiParseQuery(aiQuery, {
      smart: true,
      projectId: observeId,
      source,
    });
    if (aiFilters.length > 0) {
      const converted = aiFilters.map((f) => {
        const prop = properties.find((p) => p.id === f.field);
        return {
          field: f.field,
          fieldCategory: prop?.category || "system",
          fieldType: prop?.type || "string",
          operator: f.operator || "is",
          value: Array.isArray(f.value) ? f.value : [f.value],
        };
      });
      setRows(converted);
      onApply(converted);
      setAiQuery("");
      onClose();
    }
  }, [aiQuery, aiParseQuery, observeId, source, properties, onApply, onClose]);

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
            width: panelWidth || 560,
            borderRadius: "10px",
            mt: 0.5,
            p: 1,
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
              onChange={(e) => setAiQuery(e.target.value)}
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
                  />
                ))}
              </Stack>
            )}
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mt: 1.5 }}
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
              <Stack direction="row" spacing={1}>
                <Button
                  size="small"
                  onClick={handleClear}
                  sx={{ textTransform: "none", fontSize: 12 }}
                >
                  Clear all
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  onClick={handleApply}
                  sx={{
                    textTransform: "none",
                    fontSize: 12,
                    px: 2,
                  }}
                >
                  Apply
                </Button>
              </Stack>
            </Stack>
          </Box>
        )}

        {/* Query tab — inline token builder using same properties from dashboard API */}
        {showQueryTab && activeTab === "query" && (
          <Box sx={{ px: 0.5, pt: 0.25 }}>
            <QueryInput
              filterFields={queryFilterFields}
              fieldMap={queryFieldMap}
              onApply={handleQueryTokensChange}
              initialTokens={rows
                .filter(
                  (r) =>
                    r.field &&
                    (Array.isArray(r.value)
                      ? r.value.length > 0
                      : r.value !== "" &&
                        r.value !== undefined &&
                        r.value !== null),
                )
                .map((r) => ({
                  field: r.field,
                  operator: BASIC_TO_QUERY_OP[r.operator] || r.operator,
                  value: Array.isArray(r.value)
                    ? r.value.join(", ")
                    : r.value || "",
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
                onClick={handleClear}
                sx={{ textTransform: "none", fontSize: 12 }}
              >
                Clear all
              </Button>
              <Button
                size="small"
                variant="contained"
                onClick={handleApply}
                sx={{ textTransform: "none", fontSize: 12, px: 2 }}
              >
                Apply
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
  projectId: PropTypes.string,
  properties: PropTypes.array,
  ValuePickerOverride: PropTypes.elementType,
  showAi: PropTypes.bool,
  showQueryTab: PropTypes.bool,
  categories: PropTypes.array,
  panelWidth: PropTypes.number,
  defaultRow: PropTypes.object,
  isSimulator: PropTypes.bool,
  freeSoloValues: PropTypes.oneOfType([PropTypes.bool, PropTypes.func]),
};

export default React.memo(TraceFilterPanel);
