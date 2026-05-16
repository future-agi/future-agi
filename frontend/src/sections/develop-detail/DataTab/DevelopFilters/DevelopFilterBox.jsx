/* eslint-disable react-refresh/only-export-components */
/**
 * DevelopFilterBox — dataset row filter panel.
 *
 * Renders the LLMTracing `TraceFilterPanel` parameterised with dataset
 * columns (+ evaluation columns) and a dataset-column value picker. Filter
 * state is kept in `useDevelopFilterStore` in the legacy
 * `{columnId, filterConfig: {filterType, filterOp, filterValue}}` shape,
 * which is what the grid's API transformer expects. Translation to/from
 * TraceFilterPanel's `{field, fieldType, operator, value}` shape happens
 * inside this component only.
 */
import {
  Autocomplete,
  Box,
  Checkbox,
  Chip,
  InputAdornment,
  TextField,
  Typography,
} from "@mui/material";
import { isEqual } from "lodash";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import FilterChips from "src/sections/projects/LLMTracing/FilterChips";
import { useParams } from "src/routes/hooks";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import { useDatasetColumnValues } from "src/hooks/useDashboards";
import { getRandomId } from "src/utils/utils";
import { useDevelopFilterStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { transformFilter, validateFilter } from "./common";

// Column data types the backend can filter on.
// Audio and other media are excluded intentionally.
const ALLOWED_DATA_TYPES = new Set([
  "text",
  "integer",
  "float",
  "boolean",
  "datetime",
  "array",
]);

// Dataset column data_type → panel fieldType (normalized)
const DATA_TYPE_TO_PANEL_TYPE = {
  text: "string",
  integer: "number",
  float: "number",
  boolean: "boolean",
  datetime: "date",
  array: "array",
};

// Panel fieldType → store filterType
const PANEL_TYPE_TO_STORE_TYPE = {
  string: "text",
  number: "number",
  date: "datetime",
  boolean: "boolean",
  array: "array",
};

const formatDateInputValue = (value) => {
  if (!value) return "";
  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    return value.toISOString().slice(0, 16);
  }
  const stringValue = String(value);
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(stringValue)) {
    return stringValue.slice(0, 16);
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(stringValue)) {
    return `${stringValue}T00:00`;
  }
  return stringValue;
};

// Store filterOp → panel operator (per field type)
const opStoreToPanel = (storeOp, panelType) => {
  if (panelType === "number") {
    return (
      {
        equals: "equal_to",
        not_equals: "not_equal_to",
        greater_than: "greater_than",
        less_than: "less_than",
        greater_than_or_equal: "greater_than_or_equal",
        less_than_or_equal: "less_than_or_equal",
        between: "between",
        not_between: "not_between",
      }[storeOp] || "equal_to"
    );
  }
  if (panelType === "date") {
    return (
      {
        equals: "on",
        less_than: "before",
        greater_than: "after",
        between: "between",
        not_between: "not_between",
      }[storeOp] || "on"
    );
  }
  if (panelType === "boolean") return "is";
  if (panelType === "array") {
    return (
      {
        contains: "contains",
        not_contains: "not_contains",
        is_null: "is_empty",
        is_not_null: "is_not_empty",
      }[storeOp] || "contains"
    );
  }
  // string
  return (
    {
      equals: "is",
      not_equals: "is_not",
      contains: "contains",
      not_contains: "not_contains",
      starts_with: "starts_with",
      // Legacy op without a direct panel equivalent falls back to contains.
      ends_with: "contains",
    }[storeOp] || "is"
  );
};

// Panel operator → store filterOp
const opPanelToStore = (panelOp, panelType) => {
  if (panelType === "number") {
    return (
      {
        equal_to: "equals",
        not_equal_to: "not_equals",
        greater_than: "greater_than",
        less_than: "less_than",
        greater_than_or_equal: "greater_than_or_equal",
        less_than_or_equal: "less_than_or_equal",
        between: "between",
        not_between: "not_between",
      }[panelOp] || "equals"
    );
  }
  if (panelType === "date") {
    return (
      {
        on: "equals",
        before: "less_than",
        after: "greater_than",
        between: "between",
        not_between: "not_between",
      }[panelOp] || "equals"
    );
  }
  if (panelType === "boolean") return "equals";
  if (panelType === "array") {
    return (
      {
        contains: "contains",
        not_contains: "not_contains",
        is_empty: "is_null",
        is_not_empty: "is_not_null",
      }[panelOp] || "contains"
    );
  }
  // string
  return (
    {
      is: "equals",
      is_not: "not_equals",
      contains: "contains",
      not_contains: "not_contains",
      starts_with: "starts_with",
    }[panelOp] || "equals"
  );
};

// Store filterValue → panel value (mostly a pass-through; normalize number/date arrays)
const valueStoreToPanel = (val, panelType) => {
  if (val === undefined || val === null)
    return panelType === "number" || panelType === "date" ? "" : [];
  if (panelType === "boolean")
    return val === true || val === "true" ? "true" : "false";
  if (panelType === "date") {
    return Array.isArray(val)
      ? val.map((item) => formatDateInputValue(item))
      : formatDateInputValue(val);
  }
  return val;
};

const isNullish = (v) => v === undefined || v === null;
const valuePanelToStore = (val, panelType) => {
  if (panelType === "boolean") return val === "true" || val === true;
  if (panelType === "date") {
    if (Array.isArray(val)) {
      return val.map((item) => (item ? new Date(item) : item));
    }
    return val ? new Date(val) : "";
  }
  if (Array.isArray(val)) {
    if (panelType === "array") {
      const clean = val.filter((v) => !isNullish(v) && v !== "");
      return clean.length ? clean : "";
    }
    if (val.length === 0) return "";
    if (val.length === 1) return isNullish(val[0]) ? "" : val[0];
    if (val.every(isNullish)) return "";
    return val;
  }
  if (isNullish(val)) return "";
  return val;
};

export const storeFilterToPanel = (storeFilter, columnLookup) => {
  const col = columnLookup[storeFilter.columnId];
  const panelType = col?.panelType || "string";
  const category =
    col?.originType === "evaluation" || col?.originType === "evaluation_reason"
      ? "evaluation"
      : "dataset";
  return {
    field: storeFilter.columnId,
    fieldCategory: category,
    fieldType: panelType,
    operator: opStoreToPanel(storeFilter.filterConfig?.filterOp, panelType),
    value: valueStoreToPanel(storeFilter.filterConfig?.filterValue, panelType),
  };
};

// TraceFilterPanel's AI-filter path wraps every LLM-returned scalar in
// an array (`[value]`) to match the trace chip-picker contract. The
// dataset rows endpoint expects scalars for text/number/date/boolean
// columns — an array-valued `filter_value` hits `.lower()`/`float()`
// and is silently swallowed by the backend's try/except, returning
// every row unfiltered (TH-4400). Unwrap here so the store always
// holds the shape `_apply_filters` expects.
export const unwrapScalarValue = (value, fieldType, operator) => {
  if (!Array.isArray(value)) {
    if (value === undefined || value === null) return "";
    return value;
  }
  if (fieldType === "array") return value;
  if (operator === "between" || operator === "not_between") {
    return value.every(isNullish) ? "" : value;
  }
  const first = value[0];
  return isNullish(first) ? "" : first;
};

const FREE_TEXT_NO_OPTIONS_TEXT = "No suggestions yet — type a value to add it";

function normalizePickerValues(values) {
  const rawValues = Array.isArray(values) ? values : values ? [values] : [];
  const cleanValues = rawValues
    .map((item) => String(item ?? "").trim())
    .filter(Boolean);
  return Array.from(new Set(cleanValues));
}

export const panelFilterToStore = (panelFilter) => {
  const storeType = PANEL_TYPE_TO_STORE_TYPE[panelFilter.fieldType] || "text";
  const rawValue = valuePanelToStore(panelFilter.value, panelFilter.fieldType);
  const filterValue = unwrapScalarValue(
    rawValue,
    panelFilter.fieldType,
    panelFilter.operator,
  );
  return {
    id: getRandomId(),
    columnId: panelFilter.field,
    filterConfig: {
      filterType: storeType,
      filterOp: opPanelToStore(panelFilter.operator, panelFilter.fieldType),
      filterValue,
    },
    _meta: { parentProperty: "" },
  };
};

// Value picker for text & array columns — free-text entry with chips for array.
// For text: one string value. For array: multi-chip list (press Enter to add).
// Value picker for text & array columns. Suggestions come from the
// backend's `filter_values?source=dataset_column` endpoint, which
// returns distinct non-empty cell values for the (dataset, column)
// pair. For array/json columns the endpoint parses the JSON and
// returns element-level suggestions ("English" rather than the raw
// serialized `["English","French"]` blob), so the dropdown lines up
// with what the LLM/user actually reason about. freeSolo so users can
// still type a substring that doesn't appear in the suggestion set.
export const DatasetColumnValuePicker = ({
  fieldType,
  value,
  onChange,
  property,
  projectId, // TraceFilterPanel passes the scope id through this prop; for
  // datasets it's the dataset UUID (see DevelopFilterBox).
  freeSoloValues = false,
}) => {
  const columnId = property?.id;
  const { data: suggestions = [], isLoading } = useDatasetColumnValues({
    datasetId: projectId,
    columnId,
    enabled: Boolean(projectId && columnId),
  });
  const [inputValue, setInputValue] = useState("");

  if (fieldType === "array" || freeSoloValues) {
    const arrVal = normalizePickerValues(value);
    const suggestionValues = normalizePickerValues(suggestions);
    const customInputValue = inputValue.trim();
    const showCustomOption = Boolean(
      freeSoloValues &&
        customInputValue &&
        !suggestionValues.some(
          (suggestion) =>
            suggestion.toLowerCase() === customInputValue.toLowerCase(),
        ),
    );
    const optionsWithCustom = showCustomOption
      ? [...suggestionValues, customInputValue]
      : suggestionValues;
    const commitInputValue = (rawInput) => {
      const typedValues = String(rawInput || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      if (!typedValues.length) return false;
      onChange(Array.from(new Set([...arrVal, ...typedValues])));
      setInputValue("");
      return true;
    };

    return (
      <Autocomplete
        multiple
        freeSolo
        size="small"
        disableCloseOnSelect
        options={optionsWithCustom}
        value={arrVal}
        inputValue={inputValue}
        onInputChange={(_, newInputValue, reason) => {
          if (reason === "reset") return;
          if (newInputValue.includes(",")) {
            commitInputValue(newInputValue);
            return;
          }
          setInputValue(newInputValue);
        }}
        onChange={(_, newVal) => {
          onChange(normalizePickerValues(newVal));
        }}
        loading={isLoading}
        noOptionsText={freeSoloValues ? FREE_TEXT_NO_OPTIONS_TEXT : undefined}
        getOptionLabel={(option) => String(option ?? "")}
        isOptionEqualToValue={(option, selectedValue) =>
          String(option ?? "") === String(selectedValue ?? "")
        }
        sx={{ flex: 1, minWidth: 160, maxWidth: 320 }}
        renderOption={(props, option, { selected }) => {
          const optionValue = String(option ?? "");
          const isCustomOption =
            showCustomOption &&
            optionValue.toLowerCase() === customInputValue.toLowerCase();
          return (
            <Box
              component="li"
              {...props}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                px: 1.5,
                py: 0.75,
              }}
            >
              <Checkbox size="small" checked={selected} sx={{ p: 0 }} />
              {isCustomOption ? (
                <Typography sx={{ fontSize: 12 }}>
                  + Specify: <strong>{customInputValue}</strong>
                </Typography>
              ) : (
                <Typography noWrap sx={{ fontSize: 12 }}>
                  {optionValue}
                </Typography>
              )}
            </Box>
          );
        }}
        renderTags={(tagValue, getTagProps) =>
          tagValue.map((option, index) => (
            <Chip
              size="small"
              label={option}
              {...getTagProps({ index })}
              key={option}
              deleteIcon={<Iconify icon="mdi:close" width={10} />}
              sx={{
                height: 20,
                fontSize: 10,
                maxWidth: 100,
                "& .MuiChip-label": { px: 0.5 },
              }}
            />
          ))
        }
        renderInput={(params) => (
          <TextField
            {...params}
            placeholder={arrVal.length ? "" : "Select values..."}
            helperText={
              freeSoloValues ? "Select one or more values (multi-select)" : ""
            }
            onKeyDown={(event) => {
              if (
                (event.key === "Enter" || event.key === ",") &&
                inputValue.trim()
              ) {
                event.preventDefault();
                event.stopPropagation();
                commitInputValue(inputValue);
              }
            }}
            onBlur={() => commitInputValue(inputValue)}
            InputProps={{
              ...params.InputProps,
              sx: { fontSize: 12, minHeight: 28, py: 0 },
            }}
          />
        )}
      />
    );
  }

  // string / fallback — single text value with suggestion dropdown.
  const strVal = Array.isArray(value) ? value[0] || "" : value || "";
  return (
    <Autocomplete
      freeSolo
      size="small"
      options={suggestions}
      value={strVal}
      // onInputChange fires for both typing and option-pick so a user who
      // types a novel substring still gets it flushed to the store.
      onInputChange={(_, newVal) => onChange(newVal || "")}
      loading={isLoading}
      sx={{ flex: 1, minWidth: 140, maxWidth: 240 }}
      renderInput={(params) => (
        <TextField
          {...params}
          placeholder="Value"
          InputProps={{
            ...params.InputProps,
            sx: { fontSize: 12, height: 28 },
            startAdornment: (
              <InputAdornment position="start" sx={{ mr: 0.5 }}>
                <Iconify
                  icon="mdi:pencil-outline"
                  width={12}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
        />
      )}
    />
  );
};

export const buildProperties = (allColumns) => {
  if (!Array.isArray(allColumns)) return [];
  return allColumns
    .map((column) => {
      const colData = column?.col;
      const dataType = colData?.data_type ?? colData?.dataType;
      if (!ALLOWED_DATA_TYPES.has(dataType)) return null;
      const panelType = DATA_TYPE_TO_PANEL_TYPE[dataType] || "string";
      const originType = colData?.origin_type;
      const isEval =
        originType === "evaluation" || originType === "evaluation_reason";
      return {
        id: column.field || colData?.id,
        name: column.headerName || colData?.name || colData?.id,
        type: panelType,
        category: isEval ? "evaluation" : "dataset",
        originType,
        panelType,
      };
    })
    .filter(Boolean);
};

export const DEVELOP_FILTER_CATEGORIES = [
  { key: "all", label: "All", icon: "mdi:view-grid-outline" },
  { key: "dataset", label: "Dataset", icon: "mdi:table" },
  { key: "evaluation", label: "Evals", icon: "mdi:check-circle-outline" },
];

const DevelopFilterBox = () => {
  const {
    isDevelopFilterOpen,
    setDevelopFilterOpen,
    filters,
    setFilters,
    resetFilters,
  } = useDevelopFilterStore();
  const { dataset } = useParams();
  const { gridApi } = useDevelopDetailContext();

  const allColumns = useDatasetColumnConfig(dataset, false, true);

  const properties = useMemo(() => buildProperties(allColumns), [allColumns]);

  const columnLookup = useMemo(() => {
    const m = {};
    for (const p of properties) m[p.id] = p;
    return m;
  }, [properties]);

  // Separate lookup for chip labels — includes every column regardless of
  // data_type so we can still show a proper `display_name` for filters on
  // eval / eval_reason / otherwise-disallowed columns. Filter panel keeps
  // using `columnLookup` (which is restricted to filterable types).
  const labelLookup = useMemo(() => {
    const m = {};
    if (Array.isArray(allColumns)) {
      for (const column of allColumns) {
        const colData = column?.col;
        const id = column.field || colData?.id;
        if (!id) continue;
        m[id] = column.headerName || colData?.name || colData?.id;
      }
    }
    return m;
  }, [allColumns]);

  const [anchorEl, setAnchorEl] = useState(null);

  useEffect(() => {
    if (isDevelopFilterOpen) {
      const el = document.querySelector("[data-develop-filter-anchor]");
      setAnchorEl(el || document.body);
    } else {
      setAnchorEl(null);
    }
  }, [isDevelopFilterOpen]);

  const panelCurrentFilters = useMemo(
    () =>
      filters
        .filter((f) => f.columnId)
        .map((f) => storeFilterToPanel(f, columnLookup)),
    [filters, columnLookup],
  );

  const handleClose = useCallback(() => {
    setDevelopFilterOpen(false);
  }, [setDevelopFilterOpen]);

  // Valid filters in snake_case API shape for chip display. Inject the
  // column's human-readable name as `display_name` so FilterChips renders
  // "language is English" instead of mangling the UUID column_id via
  // _.startCase. Uses `labelLookup` (all columns, not restricted to
  // filterable data_types) so eval/reason chips also get a real label.
  // Falls back to any `display_name` already on the filter (written when
  // the filter was created) so a brief refetch window, a deleted column,
  // or a column hidden after save doesn't flash the fallback label.
  const chipFilters = useMemo(
    () =>
      filters
        .filter(validateFilter)
        .map(transformFilter)
        .map((f) => ({
          ...f,
          display_name:
            labelLookup?.[f?.column_id] ??
            columnLookup?.[f?.column_id]?.name ??
            f.display_name,
        })),
    [filters, columnLookup, labelLookup],
  );

  // Map a chip-list index back to the corresponding index in the store's
  // `filters` array (which may also contain invalid/empty rows).
  const validFilterIndices = useMemo(() => {
    const out = [];
    filters.forEach((f, i) => {
      if (validateFilter(f)) out.push(i);
    });
    return out;
  }, [filters]);

  const handleRemoveChip = useCallback(
    (chipIdx) => {
      const storeIdx = validFilterIndices[chipIdx];
      if (storeIdx === undefined) return;
      setFilters((prev) => prev.filter((_, i) => i !== storeIdx));
      if (gridApi?.current?.onFilterChanged) {
        gridApi.current.onFilterChanged();
      }
    },
    [validFilterIndices, setFilters, gridApi],
  );

  const handleClearChips = useCallback(() => {
    resetFilters();
    if (gridApi?.current?.onFilterChanged) {
      gridApi.current.onFilterChanged();
    }
  }, [resetFilters, gridApi]);

  const handleApply = useCallback(
    (newPanelFilters) => {
      const next = (newPanelFilters || []).map(panelFilterToStore);
      const safeNext = next.length
        ? next
        : [
            {
              id: getRandomId(),
              columnId: "",
              filterConfig: {
                filterType: "text",
                filterOp: "equals",
                filterValue: "",
              },
              _meta: { parentProperty: "" },
            },
          ];

      const oldValid = filters.filter(validateFilter).map(transformFilter);
      const newValid = safeNext.filter(validateFilter).map(transformFilter);
      setFilters(() => safeNext);
      if (!isEqual(oldValid, newValid) && gridApi?.current?.onFilterChanged) {
        gridApi.current.onFilterChanged();
      }
    },
    [filters, setFilters, gridApi],
  );

  return (
    <>
      <FilterChips
        extraFilters={chipFilters}
        onRemoveFilter={handleRemoveChip}
        onClearAll={handleClearChips}
        onAddFilter={() => setDevelopFilterOpen(true)}
      />
      <TraceFilterPanel
        anchorEl={anchorEl}
        open={isDevelopFilterOpen}
        onClose={handleClose}
        currentFilters={panelCurrentFilters}
        onApply={handleApply}
        properties={properties}
        ValuePickerOverride={DatasetColumnValuePicker}
        // `projectId` is TraceFilterPanel's generic "scope id" prop. For
        // datasets we thread the dataset UUID through here so:
        //   (1) DatasetColumnValuePicker can fetch per-column values
        //   (2) handleAiFilter fires smart mode (`projectId && smart`) and
        //       the backend runs the agent with per-column value grounding.
        projectId={dataset}
        source="dataset"
        showAi
        showQueryTab
        categories={DEVELOP_FILTER_CATEGORIES}
        panelWidth={560}
      />
    </>
  );
};

DevelopFilterBox.propTypes = {};

DatasetColumnValuePicker.propTypes = {
  fieldType: PropTypes.string,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  property: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
  }),
  projectId: PropTypes.string,
  freeSoloValues: PropTypes.bool,
};

export default DevelopFilterBox;
