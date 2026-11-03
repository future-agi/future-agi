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
  Button,
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
import {
  DATA_TYPE_TO_PANEL_TYPE,
  panelFilterToStore,
  storeFilterToPanel,
  unwrapScalarValue,
} from "src/components/filter/storeFilterAdapter";

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

// Re-exported so existing callers and tests keep their import path.
export { panelFilterToStore, storeFilterToPanel, unwrapScalarValue };

const FREE_TEXT_NO_OPTIONS_TEXT = "No suggestions yet — type a value to add it";

function normalizePickerValues(values) {
  const rawValues = Array.isArray(values) ? values : values ? [values] : [];
  const cleanValues = rawValues
    .map((item) => String(item ?? "").trim())
    .filter(Boolean);
  return Array.from(new Set(cleanValues));
}

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
  const {
    data: suggestions = [],
    isLoading,
    isError,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useDatasetColumnValues({
    datasetId: projectId,
    columnId,
    enabled: Boolean(projectId && columnId),
  });
  const [inputValue, setInputValue] = useState("");
  const loadMoreControl = hasNextPage ? (
    <Button
      size="small"
      variant="text"
      disabled={isFetchingNextPage}
      onClick={() => fetchNextPage()}
      sx={{ alignSelf: "flex-start", minWidth: 0, px: 0.5, mt: 0.25 }}
    >
      {isFetchingNextPage ? "Loading more values…" : "Load more values"}
    </Button>
  ) : null;

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
      <Box sx={{ flex: 1, minWidth: 160, maxWidth: 320 }}>
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
          noOptionsText={
            isError
              ? "Suggestions unavailable. Enter an exact value."
              : freeSoloValues
                ? FREE_TEXT_NO_OPTIONS_TEXT
                : undefined
          }
          getOptionLabel={(option) => String(option ?? "")}
          isOptionEqualToValue={(option, selectedValue) =>
            String(option ?? "") === String(selectedValue ?? "")
          }
          sx={{ width: "100%" }}
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
                deleteIcon={<Iconify icon="solar:close-linear" width={12} />}
                sx={{
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
              error={isError}
              helperText={
                isError
                  ? "Suggestions unavailable; typed values still work."
                  : freeSoloValues
                    ? "Select one or more values (multi-select)"
                    : ""
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
        {loadMoreControl}
      </Box>
    );
  }

  // string / fallback — single text value with suggestion dropdown.
  const strVal = Array.isArray(value) ? value[0] || "" : value || "";
  return (
    <Box sx={{ flex: 1, minWidth: 140, maxWidth: 240 }}>
      <Autocomplete
        freeSolo
        size="small"
        options={suggestions}
        value={strVal}
        // onInputChange fires for both typing and option-pick so a user who
        // types a novel substring still gets it flushed to the store.
        onInputChange={(_, newVal) => onChange(newVal || "")}
        loading={isLoading}
        noOptionsText={
          isError ? "Suggestions unavailable. Enter an exact value." : undefined
        }
        sx={{ width: "100%" }}
        renderInput={(params) => (
          <TextField
            {...params}
            placeholder="Value"
            error={isError}
            helperText={
              isError ? "Suggestions unavailable; typed values still work." : ""
            }
            InputProps={{
              ...params.InputProps,
              sx: { fontSize: 12, height: 28 },
              startAdornment: (
                <InputAdornment position="start" sx={{ mr: 0.5 }}>
                  <Iconify
                    icon="solar:pen-linear"
                    width={12}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
            }}
          />
        )}
      />
      {loadMoreControl}
    </Box>
  );
};

export const buildProperties = (allColumns) => {
  if (!Array.isArray(allColumns)) return [];
  return allColumns
    .map((column) => {
      const colData = column?.col;
      const dataType = colData?.data_type;
      if (!ALLOWED_DATA_TYPES.has(dataType)) return null;
      const panelType = DATA_TYPE_TO_PANEL_TYPE[dataType] || "string";
      const originType = colData?.origin_type;
      const isEval =
        originType === "evaluation" || originType === "evaluation_reason";
      // Dataset-column registry identity is the immutable column UUID. AG Grid
      // currently uses the same UUID as `field`, but prefer the catalog's
      // canonical `col.id` so a display/accessor alias can never leak into
      // property lookup, persisted filter state, or filter_values requests.
      const columnId = colData?.id || column.field;
      return {
        id: columnId,
        registryId: `dataset_column:${columnId}`,
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
  { key: "all", label: "All", icon: "solar:widget-4-linear" },
  { key: "dataset", label: "Dataset", icon: "solar:table-linear" },
  { key: "evaluation", label: "Evals", icon: "solar:check-circle-linear" },
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
        const id = colData?.id || column.field;
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
        onChipClick={() => setDevelopFilterOpen(true)}
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
