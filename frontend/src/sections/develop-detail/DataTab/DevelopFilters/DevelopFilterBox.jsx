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
import {
  useDatasetColumnConfig,
  useGetJsonColumnSchema,
} from "src/api/develop/develop-detail";
import { schemaPathOptions } from "src/sections/common/Values/PathInput";
import { dominantKind } from "src/sections/common/Values/inferSchema";
import { useDatasetColumnValues } from "src/hooks/useDashboards";
import { getRandomId } from "src/utils/utils";
import { isPrototypeMode } from "src/utils/prototype";
import { useDevelopFilterStore } from "../../states";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import { useAssignments, useTranslateInfo } from "src/api/develop/row-collab";
import CollabValueSelect from "./CollabValueSelect";
import {
  COLLAB_CATEGORY,
  collabChipValue,
  collabFieldProperties,
} from "./collabFilterFields";
import {
  PATH_FIELD_SEPARATOR,
  transformFilter,
  validateFilter,
} from "./common";

// W5 grid seam G4: the AI box posts /model-hub/ai-filter/, which no
// prototype handler answers, so prototype builds (and tests) leave it out.
// The one prototype flag is utils/prototype.
const PROTOTYPE_BUILD = isPrototypeMode() || import.meta.env.MODE === "test";
// HO D3: the live collaboration keys (assigned to, comments, language) are
// answered where the W4 reads are served (prototype mode).
const COLLAB_FIELDS_SERVED = isPrototypeMode();

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

const DEFAULT_OP_BY_PANEL_TYPE = {
  number: "equals",
  date: "equals",
  boolean: "equals",
  array: "contains",
  string: "in",
  text: "in",
};

// The shared TraceFilterPanel stores canonical backend operator values.
const opStoreToPanel = (storeOp, panelType) => {
  return storeOp || DEFAULT_OP_BY_PANEL_TYPE[panelType] || "equals";
};

const opPanelToStore = (panelOp, panelType) => {
  return panelOp || DEFAULT_OP_BY_PANEL_TYPE[panelType] || "equals";
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
const valuePanelToStore = (val, panelType, operator) => {
  if (panelType === "boolean") return val === "true" || val === true;
  if (panelType === "date") {
    if (Array.isArray(val)) {
      return val.map((item) => (item ? new Date(item) : item));
    }
    return val ? new Date(val) : "";
  }
  if (Array.isArray(val)) {
    if (operator === "in" || operator === "not_in") {
      const clean = val.filter((v) => !isNullish(v) && v !== "");
      return clean.length ? clean : "";
    }
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
  // in/not_in require a list value; wrap a single typed scalar so it still applies.
  if (operator === "in" || operator === "not_in") {
    return val === "" ? "" : [val];
  }
  return val;
};

export const storeFilterToPanel = (storeFilter, columnLookup) => {
  const col = columnLookup[storeFilter.columnId];
  const panelType = col?.panelType || "string";
  const category =
    col?.category ??
    (col?.originType === "evaluation" || col?.originType === "evaluation_reason"
      ? "evaluation"
      : "dataset");
  return {
    field: storeFilter.columnId,
    registryId:
      storeFilter.registryId ||
      storeFilter.propertyId ||
      storeFilter.property_id ||
      col?.registryId,
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
  if (operator === "in" || operator === "not_in") {
    const clean = value.filter((item) => !isNullish(item) && item !== "");
    return clean.length ? clean : "";
  }
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

// IT-R5 G5 P1: the panel stores a typed string as the list operator `in`
// (its picker can collect several values), so one value used to read
// `is one of e`. For the chip only, one value reads as `is` / `is not`;
// the stored predicate and the request keep the canonical list shape.
export const singleValueChip = (apiFilter) => {
  const op = apiFilter?.filter_config?.filter_op;
  const value = apiFilter?.filter_config?.filter_value;
  if ((op !== "in" && op !== "not_in") || !Array.isArray(value)) {
    return apiFilter;
  }
  if (value.length !== 1) return apiFilter;
  return {
    ...apiFilter,
    filter_config: {
      ...apiFilter.filter_config,
      filter_op: op === "in" ? "equals" : "not_equals",
      filter_value: value[0],
    },
  };
};

export const panelFilterToStore = (panelFilter) => {
  const storeType = PANEL_TYPE_TO_STORE_TYPE[panelFilter.fieldType] || "text";
  const rawValue = valuePanelToStore(
    panelFilter.value,
    panelFilter.fieldType,
    panelFilter.operator,
  );
  const filterValue = unwrapScalarValue(
    rawValue,
    panelFilter.fieldType,
    panelFilter.operator,
  );
  return {
    id: getRandomId(),
    columnId: panelFilter.field,
    ...(panelFilter.registryId && { registryId: panelFilter.registryId }),
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
  // FA Path properties (R2 P6) suggest their enum values; no fetch. HO D3:
  // a Collaboration field picks one of the server's values by its label.
  const columnId = property?.path || property?.enumLabels ? null : property?.id;
  const {
    data: fetchedSuggestions = [],
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
  const suggestions = property?.path
    ? property.enumValues ?? []
    : fetchedSuggestions;
  const [inputValue, setInputValue] = useState("");
  const stringValue = Array.isArray(value) ? value[0] || "" : value || "";
  const [stringInputValue, setStringInputValue] = useState(stringValue);
  useEffect(() => {
    setStringInputValue(stringValue);
  }, [stringValue]);
  if (property?.enumLabels)
    return (
      <CollabValueSelect
        value={value}
        onChange={onChange}
        property={property}
      />
    );
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

  // string / fallback — single text value with suggestion dropdown. Keep the
  // text field's draft separate from the selected Autocomplete value: updating
  // the row on every keystroke makes MUI replay the controlled value and can
  // leave only the last character in the filter. Enter and blur are the commit
  // points for an exact value; choosing a suggestion still commits immediately.
  const strVal = stringValue;
  const commitTypedValue = () => {
    if (stringInputValue !== strVal) onChange(stringInputValue);
  };
  return (
    <Box sx={{ flex: 1, minWidth: 140, maxWidth: 240 }}>
      <Autocomplete
        freeSolo
        size="small"
        options={suggestions}
        value={strVal}
        inputValue={stringInputValue}
        onInputChange={(_, newVal) => setStringInputValue(newVal || "")}
        onChange={(_, newVal) => {
          const nextValue =
            typeof newVal === "string"
              ? newVal
              : newVal == null
                ? ""
                : String(newVal);
          setStringInputValue(nextValue);
          onChange(nextValue);
        }}
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
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                event.stopPropagation();
                commitTypedValue();
              }
            }}
            onBlur={commitTypedValue}
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

const PATH_PANEL_TYPE = { number: "number", boolean: "boolean" };
const MAX_PATHS_PER_COLUMN = 40;

/**
 * Filter fields for typed JSON paths (R2-SPEC P6 §3.3), from the
 * json-schema's `paths[]`: `metadata.customer.tier` (string),
 * `expected.citations[0].page` (number), `conversation[*].tool_calls[*]
 * .function.name` (any of a list → `contains`). Field id
 * `<columnId>::<path>`; the API filter carries `filter_config.path`.
 */
export const buildPathProperties = (schemas) => {
  const out = [];
  const perColumn = new Map();
  Object.entries(schemas ?? {}).forEach(([columnId, entry]) => {
    schemaPathOptions({ [columnId]: entry }).forEach((p) => {
      const kind = dominantKind(p.kinds);
      if (kind === "object" || kind === "null") return;
      const n = perColumn.get(columnId) ?? 0;
      if (n >= MAX_PATHS_PER_COLUMN) return;
      perColumn.set(columnId, n + 1);
      const multi = p.rel.includes("[*]") || kind === "array";
      const panelType = multi ? "array" : PATH_PANEL_TYPE[kind] ?? "string";
      out.push({
        id: `${columnId}${PATH_FIELD_SEPARATOR}${p.rel}`,
        name: p.path,
        type: panelType,
        category: "dataset",
        originType: "OTHERS",
        panelType,
        path: p.rel,
        enumValues: p.enum_values ?? [],
      });
    });
  });
  return out;
};

export const DEVELOP_FILTER_CATEGORIES = [
  { key: "all", label: "All", icon: "mdi:view-grid-outline" },
  { key: "dataset", label: "Dataset", icon: "mdi:table" },
  { key: "evaluation", label: "Evals", icon: "mdi:check-circle-outline" },
];

const RESERVED_CATEGORIES = new Set(["all", "evaluation", "system"]);

/** The panel's category for a dataset column: its own name (IT-R1 G5 P9). */
const columnCategory = (name) => {
  const key = String(name ?? "").trim();
  if (!key) return "dataset";
  return RESERVED_CATEGORIES.has(key.toLowerCase()) ? `${key} column` : key;
};

/**
 * IT-R1 G5 P9: properties grouped by column. Each dataset column and its
 * typed paths share one category named after the column (`conversation`,
 * `expected`, `metadata` …), so the sidebar lists columns and the item tag
 * names the column instead of repeating `Dataset` 70 times. Each column is
 * followed by its own paths. Evals stay `evaluation`. Pure.
 */
export function groupPropertiesByColumn(
  columnProps,
  pathProps,
  columnNames = {},
) {
  const byColumn = new Map();
  (pathProps ?? []).forEach((p) => {
    const colId = String(p.id).split(PATH_FIELD_SEPARATOR)[0];
    if (!byColumn.has(colId)) byColumn.set(colId, []);
    byColumn.get(colId).push(p);
  });
  const out = [];
  const used = new Set();
  // Plain columns (no nested paths) share one `columns` group; a column with
  // paths is its own group, the column first, then its paths.
  (columnProps ?? []).forEach((c) => {
    const paths = byColumn.get(c.id) ?? [];
    const cat =
      c.category === "evaluation"
        ? "evaluation"
        : paths.length
          ? columnCategory(c.name)
          : "columns";
    out.push({ ...c, category: cat });
    paths.forEach((p) => {
      out.push({ ...p, category: cat });
      used.add(p.id);
    });
  });
  // Paths of json / conversation columns (not filterable as a whole).
  byColumn.forEach((paths, colId) => {
    const left = paths.filter((p) => !used.has(p.id));
    if (!left.length) return;
    const cat = columnCategory(
      columnNames[colId] ?? left[0].name.split(".")[0],
    );
    left.forEach((p) => out.push({ ...p, category: cat }));
  });
  return out;
}

/** Sidebar: All, one entry per column (grid order), Evals. Pure. */
export function columnCategories(properties) {
  const keys = [];
  (properties ?? []).forEach((p) => {
    if (p.category && p.category !== "evaluation" && !keys.includes(p.category))
      keys.push(p.category);
  });
  const hasEvals = (properties ?? []).some((p) => p.category === "evaluation");
  return [
    DEVELOP_FILTER_CATEGORIES[0],
    ...keys.map((k) =>
      k === "columns"
        ? { key: k, label: "Columns", icon: "mdi:table" }
        : { key: k, label: k, icon: "mdi:code-braces" },
    ),
    ...(hasEvals ? [DEVELOP_FILTER_CATEGORIES[2]] : []),
  ];
}

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

  const { data: jsonSchemas } = useGetJsonColumnSchema(dataset);
  const collabOpts = { enabled: COLLAB_FIELDS_SERVED && Boolean(dataset) };
  const { data: assignmentsRead } = useAssignments(dataset, {}, collabOpts);
  const { data: translateRead } = useTranslateInfo(
    dataset,
    undefined,
    collabOpts,
  );
  const collabProps = useMemo(
    () =>
      COLLAB_FIELDS_SERVED
        ? collabFieldProperties({
            assignments: assignmentsRead,
            translate: translateRead,
          })
        : [],
    [assignmentsRead, translateRead],
  );
  const properties = useMemo(() => {
    const base = buildProperties(allColumns);
    // A column made from a path (`Column from path…`) wins over the path.
    const names = new Set(base.map((p) => p.name));
    const columnNames = Object.fromEntries(
      (allColumns ?? []).map((c) => [
        c?.col?.id ?? c?.field,
        c?.col?.name ?? c?.headerName,
      ]),
    );
    return [
      ...groupPropertiesByColumn(
        base,
        buildPathProperties(jsonSchemas).filter((p) => !names.has(p.name)),
        columnNames,
      ),
      ...collabProps,
    ];
  }, [allColumns, jsonSchemas, collabProps]);
  const categories = useMemo(
    () =>
      columnCategories(properties).map((c) =>
        c.key === COLLAB_CATEGORY.key ? COLLAB_CATEGORY : c,
      ),
    [properties],
  );

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
      filters.filter(validateFilter).map((storeFilter) => {
        const f = collabChipValue(
          singleValueChip(transformFilter(storeFilter)),
          collabProps,
        );
        return {
          ...f,
          display_name: f.filter_config?.path
            ? columnLookup?.[storeFilter.columnId]?.name ?? f.display_name
            : labelLookup?.[f?.column_id] ??
              columnLookup?.[f?.column_id]?.name ??
              f.display_name,
        };
      }),
    [filters, columnLookup, labelLookup, collabProps],
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
        open={isDevelopFilterOpen && Boolean(anchorEl)}
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
        // W5 grid seam G4: absent in prototype builds (see PROTOTYPE_BUILD).
        showAi={!PROTOTYPE_BUILD}
        showQueryTab
        aiPlaceholder="e.g. rows where metadata.customer.tier is enterprise"
        closeOnApply
        categories={categories}
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
    path: PropTypes.string,
    enumValues: PropTypes.array,
    enumLabels: PropTypes.object,
  }),
  projectId: PropTypes.string,
  freeSoloValues: PropTypes.bool,
};

export default DevelopFilterBox;
