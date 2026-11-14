/* eslint-disable react/prop-types */
/**
 * FilterPanel — Reusable filter popover with AI, Basic, and Query modes.
 *
 * Usage:
 *   <FilterPanel
 *     anchorEl={anchorEl}
 *     open={open}
 *     onClose={onClose}
 *     filterFields={[
 *       { value: "status", label: "Status", type: "enum", choices: ["OK", "ERROR"] },
 *       { value: "model", label: "Model", type: "string" },
 *     ]}
 *     currentFilters={filters}
 *     onApply={setFilters}
 *     aiPlaceholder="e.g. 'show traces with errors'"
 *   />
 *
 * Pass `basicOnly` to reduce the popover to just the filter rows — no tab
 * strip, no AI query box, no section caption.
 * A field may carry `choiceLabels: {value: label}` when its `choices` are
 * opaque keys (enum values, UUIDs) that should never be shown to the user.
 */
import {
  Box,
  Button,
  Chip,
  IconButton,
  InputAdornment,
  MenuItem,
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
import Iconify from "src/components/iconify";
import { buildPropertyRegistryId } from "src/hooks/useDashboards";
import { useAIFilter } from "src/hooks/use-ai-filter";
import QueryInput from "./QueryInput";

// ---------------------------------------------------------------------------
// Operators
// ---------------------------------------------------------------------------
const STRING_OPERATORS = [
  { value: "contains", label: "Contains" },
  { value: "equals", label: "Equals" },
  { value: "not_equals", label: "Not equals" },
  { value: "starts_with", label: "Starts with" },
  { value: "not_contains", label: "Does not contain" },
];

const ENUM_OPERATORS = [
  { value: "is", label: "Is" },
  { value: "is_not", label: "Is not" },
];

function aiPropertyCategory(field) {
  if (field.category) return field.category;
  const kind = field.propertyKind || field.property_kind;
  if (kind === "custom_attribute") return "attribute";
  if (kind === "eval" || kind === "eval_config" || kind === "eval_template")
    return "eval";
  if (kind === "annotation") return "annotation";
  if (kind === "dataset_column") return "dataset_column";
  return "system";
}

function aiPropertyMetricType(field) {
  if (field.metricType || field.metric_type)
    return field.metricType || field.metric_type;
  const category = aiPropertyCategory(field);
  if (category === "attribute") return "custom_attribute";
  if (category === "eval") return "eval_metric";
  if (category === "annotation") return "annotation_metric";
  if (category === "dataset_column") return "custom_column";
  return "system_metric";
}

function getOperators(fieldDef) {
  const fieldType = typeof fieldDef === "string" ? fieldDef : fieldDef?.type;
  const base = fieldType === "enum" ? ENUM_OPERATORS : STRING_OPERATORS;
  const allowed =
    typeof fieldDef === "object" && Array.isArray(fieldDef?.operators)
      ? fieldDef.operators
      : null;
  return allowed ? base.filter((op) => allowed.includes(op.value)) : base;
}

// ---------------------------------------------------------------------------
// NLP parser — local, no LLM dependency
// ---------------------------------------------------------------------------
function parseNaturalLanguage(query, filterFields, fieldMap) {
  const q = query.toLowerCase().trim();
  if (!q) return [];

  const rows = [];
  const usedFields = new Set();

  // Try each field for a match
  for (const field of filterFields) {
    const label = field.label.toLowerCase();
    const val = field.value.toLowerCase();

    // Pattern: "field is/= value"
    const regex = new RegExp(
      `(?:${label}|${val})\\s+(?:is|=|:)\\s+['""]?(.+?)['""]?(?:\\s+and|$)`,
      "i",
    );
    const match = q.match(regex);
    if (match && !usedFields.has(field.value)) {
      const matchedValue = match[1].trim();
      if (field.type === "enum") {
        const validChoice = field.choices?.find(
          (c) => c.toLowerCase() === matchedValue.toLowerCase(),
        );
        if (validChoice) {
          rows.push({ field: field.value, operator: "is", value: validChoice });
          usedFields.add(field.value);
        }
      } else {
        rows.push({
          field: field.value,
          operator: "contains",
          value: matchedValue,
        });
        usedFields.add(field.value);
      }
    }
  }

  // Keyword-based enum matching
  for (const field of filterFields) {
    if (
      field.type === "enum" &&
      field.choices &&
      !usedFields.has(field.value)
    ) {
      for (const choice of field.choices) {
        if (q.includes(choice.toLowerCase())) {
          rows.push({ field: field.value, operator: "is", value: choice });
          usedFields.add(field.value);
          break;
        }
      }
    }
  }

  // Fallback: first string field search
  if (rows.length === 0) {
    const nameField =
      filterFields.find((f) => f.type === "string") || filterFields[0];
    rows.push({
      field: nameField.value,
      operator: "contains",
      value: query.trim(),
    });
  }

  return rows;
}

// ---------------------------------------------------------------------------
// EnumValuePicker — checkbox multi-select popover (matches trace filter design)
// ---------------------------------------------------------------------------
function EnumValuePicker({
  choices,
  value = [],
  onChange,
  single = false,
  choiceLabels,
}) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");

  // `choices` holds the values sent to the API. When a field supplies
  // `choiceLabels`, those raw values are opaque to the user (enum keys, UUIDs)
  // and only the mapped label is ever shown or searched.
  const labelOf = useCallback(
    (choice) => choiceLabels?.[choice] ?? choice,
    [choiceLabels],
  );

  const filtered = useMemo(() => {
    if (!search) return choices;
    const q = search.toLowerCase();
    return choices.filter((c) => labelOf(c).toLowerCase().includes(q));
  }, [choices, search, labelOf]);

  const toggle = useCallback(
    (val) => {
      if (single) {
        onChange(value.includes(val) ? [] : [val]);
        setAnchorEl(null);
        setSearch("");
        return;
      }
      onChange(
        value.includes(val) ? value.filter((v) => v !== val) : [...value, val],
      );
    },
    [value, onChange, single],
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
          minHeight: 30,
          minWidth: 130,
          flex: 1,
          maxWidth: 250,
          px: 1,
          py: 0.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
          cursor: "pointer",
          "&:hover": { borderColor: "text.disabled" },
        }}
      >
        {value.length === 0 ? (
          <Typography sx={{ fontSize: 12, color: "text.disabled", flex: 1 }}>
            Select values...
          </Typography>
        ) : (
          <>
            {value.slice(0, 2).map((v) => (
              <Chip
                key={v}
                label={labelOf(v)}
                size="small"
                onDelete={(e) => {
                  e.stopPropagation();
                  onChange(value.filter((x) => x !== v));
                }}
                deleteIcon={<Iconify icon="mdi:close" width={10} />}
                sx={{
                  height: 20,
                  fontSize: 10,
                  maxWidth: 80,
                  "& .MuiChip-label": { px: 0.5 },
                }}
              />
            ))}
            {value.length > 2 && (
              <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
                +{value.length - 2}
              </Typography>
            )}
          </>
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
            {single
              ? "Select a value"
              : "Select one or more values (multi-select)"}
          </Typography>
        </Box>
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            maxHeight: 280,
            overflow: "auto",
          }}
        >
          {/* Select all matching */}
          {!single && search && filtered.length > 0 && (
            <Box
              onClick={() => {
                const allFiltered = filtered.filter((o) => !value.includes(o));
                if (allFiltered.length > 0)
                  onChange([...value, ...allFiltered]);
                else onChange(value.filter((v) => !filtered.includes(v)));
              }}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                px: 1.5,
                py: 0.75,
                cursor: "pointer",
                bgcolor: "action.hover",
                borderBottom: "1px solid",
                borderColor: "divider",
                "&:hover": { bgcolor: "action.selected" },
              }}
            >
              <Iconify
                icon={
                  filtered.every((o) => value.includes(o))
                    ? "mdi:checkbox-intermediate"
                    : "mdi:checkbox-blank-outline"
                }
                width={18}
                sx={{ color: "primary.main", flexShrink: 0 }}
              />
              <Typography
                sx={{ fontSize: 12, color: "primary.main", fontWeight: 600 }}
              >
                Select all matching in list ({filtered.length})
              </Typography>
            </Box>
          )}

          {/* Specify custom value. Suppressed for label-mapped fields: there the
              choices are opaque keys, so a typed string is never a valid value. */}
          {search && !choiceLabels && !choices.includes(search) && (
            <Box
              onClick={() => {
                if (!value.includes(search)) {
                  onChange([...value, search]);
                  setSearch("");
                }
              }}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                px: 1.5,
                py: 0.75,
                cursor: "pointer",
                borderBottom: "1px solid",
                borderColor: "divider",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Iconify
                icon={
                  value.includes(search)
                    ? "mdi:checkbox-marked"
                    : "mdi:checkbox-blank-outline"
                }
                width={18}
                sx={{
                  color: value.includes(search)
                    ? "primary.main"
                    : "text.secondary",
                  flexShrink: 0,
                }}
              />
              <Typography sx={{ fontSize: 12 }}>
                Specify: <strong>{search}</strong>
              </Typography>
            </Box>
          )}

          {filtered.length === 0 && !search && (
            <Typography
              sx={{
                p: 1.5,
                textAlign: "center",
                fontSize: 12,
                color: "text.disabled",
              }}
            >
              No values found
            </Typography>
          )}
          {filtered.map((opt) => {
            const isSelected = value.includes(opt);
            return (
              <Box
                key={opt}
                onClick={() => toggle(opt)}
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
                    single
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
                <Typography
                  noWrap
                  sx={{
                    fontSize: 12,
                    flex: 1,
                    fontWeight: isSelected ? 600 : 400,
                  }}
                >
                  {labelOf(opt)}
                </Typography>
              </Box>
            );
          })}
        </Box>
        {value.length > 0 && (
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              px: 1.5,
              py: 0.75,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
              {value.length} selected
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
        )}
      </Popover>
    </>
  );
}

// ---------------------------------------------------------------------------
// FilterRow — single row: field → operator → value
// ---------------------------------------------------------------------------
function FilterRow({
  filter,
  index,
  filterFields,
  fieldMap,
  onChange,
  onRemove,
  takenSingleFields,
}) {
  const fieldDef = fieldMap[filter.field] || filterFields[0];
  // No field to bind to (empty catalogue) — render nothing rather than reading
  // `.type` off undefined. The Basic tab shows an empty-state in this case.
  if (!fieldDef) return null;
  const operators = getOperators(fieldDef);

  return (
    <Stack direction="row" alignItems="center" gap={0.5}>
      <Select
        size="small"
        value={filter.field}
        onChange={(e) => {
          const newField = fieldMap[e.target.value];
          onChange(index, {
            field: e.target.value,
            operator: newField?.type === "enum" ? "is" : "contains",
            value: newField?.type === "enum" ? [] : "",
          });
        }}
        sx={{ minWidth: 100, fontSize: 12, height: 30 }}
        MenuProps={{ PaperProps: { sx: { maxHeight: 280 } } }}
      >
        {filterFields.map((f) => (
          <MenuItem
            key={f.value}
            value={f.value}
            disabled={
              f.value !== filter.field && takenSingleFields?.has(f.value)
            }
          >
            {f.label}
          </MenuItem>
        ))}
      </Select>

      <Select
        size="small"
        value={filter.operator}
        onChange={(e) =>
          onChange(index, { ...filter, operator: e.target.value })
        }
        sx={{ minWidth: 110, fontSize: 12, height: 30 }}
      >
        {operators.map((op) => (
          <MenuItem key={op.value} value={op.value}>
            {op.label}
          </MenuItem>
        ))}
      </Select>

      {fieldDef.type === "enum" ? (
        <EnumValuePicker
          choices={fieldDef.choices || []}
          choiceLabels={fieldDef.choiceLabels}
          single={fieldDef.single}
          value={
            Array.isArray(filter.value)
              ? filter.value
              : filter.value
                ? [filter.value]
                : []
          }
          onChange={(newVal) => onChange(index, { ...filter, value: newVal })}
        />
      ) : (
        <TextField
          size="small"
          placeholder="Enter value"
          value={filter.value}
          onChange={(e) =>
            onChange(index, { ...filter, value: e.target.value })
          }
          sx={{
            minWidth: 100,
            "& .MuiInputBase-root": { fontSize: 12, height: 30 },
          }}
        />
      )}

      <IconButton size="small" onClick={() => onRemove(index)} sx={{ p: 0.25 }}>
        <Iconify icon="mdi:close" width={14} />
      </IconButton>
    </Stack>
  );
}

FilterRow.propTypes = {
  filter: PropTypes.object.isRequired,
  index: PropTypes.number.isRequired,
  filterFields: PropTypes.array.isRequired,
  fieldMap: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

// Collapse rows into the `{field: [values]}` shape callers receive. Rows with
// no value drop out; an all-empty set applies as null rather than `{}`.
const buildFilterResult = (rows) => {
  const result = {};
  for (const row of rows) {
    const val = row.value;
    const isEmpty = !val || (Array.isArray(val) && val.length === 0);
    if (isEmpty) continue;
    const values = Array.isArray(val) ? val : [val];
    const isNeg = row.operator === "is_not" || row.operator === "not_equals";
    const key = isNeg ? `${row.field}_not` : row.field;
    if (!result[key]) result[key] = [];
    // Rows sharing a key merge, so a value picked in two of them would
    // otherwise go out twice.
    for (const v of values) {
      if (!result[key].includes(v)) result[key].push(v);
    }
  }
  return Object.keys(result).length > 0 ? result : null;
};

// Key order follows row order, which the user can change without changing the
// filter, so sort before comparing.
const serializeFilters = (result) =>
  result
    ? JSON.stringify(
        Object.keys(result)
          .sort()
          .map((k) => [k, result[k]]),
      )
    : "null";

// ---------------------------------------------------------------------------
// FilterPanel — main component
// ---------------------------------------------------------------------------
const FilterPanel = ({
  anchorEl,
  open,
  onClose,
  filterFields,
  currentFilters,
  onApply,
  aiPlaceholder = "Ask AI — e.g. 'show me items with errors'",
  width = 420,
  // Optional smart-mode wiring. When the caller passes a `projectId`,
  // the AI filter call goes through the agentic backend (`mode=smart`),
  // which fetches real CH values and grounds the LLM's answer. Without
  // a `projectId` this component retains its non-smart local-parser path.
  projectId,
  source = "traces",
  // Render the filter rows alone — no tabs, no AI box, no caption. The Query
  // tab and AI parsing are power-user surfaces that not every list needs.
  basicOnly = false,
  // Hide just the AI filter box (keep Basic + Query) where the grounded AI
  // backend isn't wired for this source — e.g. scenarios, until model-hub's
  // ai-filter accepts a `scenarios` source.
  showAiFilter = true,
  // "bottom-start" grows the popover rightwards from the trigger; use
  // "bottom-end" when the trigger sits near the right edge of the viewport.
  placement = "bottom-start",
}) => {
  const popoverEdge = placement === "bottom-end" ? "right" : "left";
  const fieldMap = useMemo(
    () => Object.fromEntries(filterFields.map((f) => [f.value, f])),
    [filterFields],
  );

  const [activeTab, setActiveTab] = useState("basic");
  const [aiQuery, setAiQuery] = useState("");

  const aiSchema = useMemo(
    () =>
      filterFields.map((f) => ({
        field: f.value,
        property_id: buildPropertyRegistryId({
          propertyId: f.registryId || f.property_id,
          metricName: f.value,
          metricType: aiPropertyMetricType(f),
          source: f.propertySource || f.property_source || source,
        }),
        label: f.label,
        category: aiPropertyCategory(f),
        type: f.type,
        operators:
          f.type === "enum"
            ? ["is", "is_not"]
            : ["contains", "equals", "starts_with", "not_contains"],
        ...(f.choices ? { choices: f.choices } : {}),
        // choiceLabels is an optional {value: humanLabel} map. We forward
        // it as choice_labels so the AI filter can resolve human labels
        // (e.g. user types "Chat") back to the canonical API value
        // (e.g. "text"). See PersonaListView.simulation_type for the
        // canonical example.
        ...(f.choiceLabels ? { choice_labels: f.choiceLabels } : {}),
      })),
    [filterFields, source],
  );

  const {
    parseQuery: aiParseQuery,
    loading: aiLoading,
    error: aiError,
  } = useAIFilter(aiSchema);

  const defaultRow = useMemo(() => {
    const first = filterFields[0];
    return {
      field: first?.value || "",
      operator: first?.type === "enum" ? "is" : "contains",
      value: "",
    };
  }, [filterFields]);

  const [rows, setRows] = useState([{ ...defaultRow }]);
  const applyTimerRef = useRef(null);
  const lastAppliedRef = useRef(undefined);

  useEffect(() => {
    if (!open) return;
    let initialRows;
    if (
      currentFilters &&
      typeof currentFilters === "object" &&
      !Array.isArray(currentFilters)
    ) {
      // Convert object-style filters to rows
      const initial = [];
      for (const [key, val] of Object.entries(currentFilters)) {
        const isNeg = key.endsWith("_not");
        const field = isNeg ? key.slice(0, -4) : key;
        const fieldDef = fieldMap[field];
        if (Array.isArray(val)) {
          const op = isNeg
            ? "is_not"
            : fieldDef?.type === "enum"
              ? "is"
              : "contains";
          if (fieldDef?.type === "enum") {
            // An enum row holds the whole set and shows it as chips, so keep
            // the values together — one row per value would come back as a
            // pile of identical rows the user never created.
            const values = fieldDef.single ? val.slice(0, 1) : val;
            if (values.length > 0)
              initial.push({ field, operator: op, value: values });
          } else {
            // A text row is a single input, so each value needs its own.
            val.forEach((v) => initial.push({ field, operator: op, value: v }));
          }
        } else if (val) {
          initial.push({
            field,
            operator: isNeg ? "not_equals" : "contains",
            value: val,
          });
        }
      }
      initialRows = initial.length > 0 ? initial : [{ ...defaultRow }];
    } else if (Array.isArray(currentFilters) && currentFilters.length > 0) {
      initialRows = [...currentFilters];
    } else {
      initialRows = [{ ...defaultRow }];
    }
    setRows(initialRows);
    // Seed the guard with what these rows would apply to, so merely opening
    // the panel doesn't re-emit filters the caller already holds.
    lastAppliedRef.current = serializeFilters(buildFilterResult(initialRows));
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-apply on row changes (debounced)
  useEffect(() => {
    if (!open) return;
    if (applyTimerRef.current) clearTimeout(applyTimerRef.current);
    applyTimerRef.current = setTimeout(() => {
      const result = buildFilterResult(rows);
      // `result` is rebuilt on every run, so callers keying off its identity
      // (an AG Grid datasource, a memo) would refetch even when nothing
      // changed. Compare by value and stay quiet when it hasn't.
      const serialized = serializeFilters(result);
      if (serialized === lastAppliedRef.current) return;
      lastAppliedRef.current = serialized;
      onApply(result);
    }, 400);
    return () => {
      if (applyTimerRef.current) clearTimeout(applyTimerRef.current);
    };
  }, [rows, open]); // eslint-disable-line react-hooks/exhaustive-deps

  // A `single` field holds exactly one value, so a second row targeting it can
  // never be honoured — apply merges the rows and the caller keeps one value
  // while the UI goes on showing both as active.
  const takenSingleFields = useMemo(
    () =>
      new Set(
        rows.filter((r) => fieldMap[r.field]?.single).map((r) => r.field),
      ),
    [rows, fieldMap],
  );

  const nextAvailableField = useMemo(
    () =>
      filterFields.find((f) => !(f.single && takenSingleFields.has(f.value))),
    [filterFields, takenSingleFields],
  );

  const handleAddRow = useCallback(() => {
    if (!nextAvailableField) return;
    setRows((prev) => [
      ...prev,
      {
        field: nextAvailableField.value,
        operator: nextAvailableField.type === "enum" ? "is" : "contains",
        value: nextAvailableField.type === "enum" ? [] : "",
      },
    ]);
  }, [nextAvailableField]);

  const handleUpdateRow = useCallback((index, newRow) => {
    setRows((prev) => prev.map((r, i) => (i === index ? newRow : r)));
  }, []);

  const handleRemoveRow = useCallback(
    (index) => {
      setRows((prev) => {
        const next = prev.filter((_, i) => i !== index);
        return next.length === 0 ? [{ ...defaultRow }] : next;
      });
    },
    [defaultRow],
  );

  const handleAiFilter = useCallback(async () => {
    if (!aiQuery.trim()) return;
    let aiFilters;
    try {
      aiFilters = await aiParseQuery(
        aiQuery,
        projectId ? { smart: true, projectId, source } : undefined,
      );
    } catch {
      // A smart request that could not prove its value vocabulary must not
      // silently become an ungrounded local literal filter.
      return;
    }
    const parsed =
      aiFilters.length > 0
        ? aiFilters
        : projectId
          ? []
          : parseNaturalLanguage(aiQuery, filterFields, fieldMap);
    setRows(parsed);
    setAiQuery("");
  }, [aiQuery, aiParseQuery, filterFields, fieldMap, projectId, source]);

  const handleClear = useCallback(() => {
    setRows([{ ...defaultRow }]);
    lastAppliedRef.current = serializeFilters(null);
    onApply(null);
    onClose();
  }, [defaultRow, onApply, onClose]);

  const handleApplyFromNlp = useCallback((nlpRows) => {
    setRows(nlpRows);
  }, []);

  const activeFilterCount = useMemo(
    () =>
      rows.filter((r) => {
        if (Array.isArray(r.value)) return r.value.length > 0;
        return !!r.value;
      }).length,
    [rows],
  );

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: popoverEdge }}
      transformOrigin={{ vertical: "top", horizontal: popoverEdge }}
      slotProps={{
        paper: {
          sx: {
            width,
            p: 1,
            borderRadius: "8px",
            boxShadow: "1px 1px 12px 10px rgba(0,0,0,0.04)",
          },
        },
      }}
    >
      <Stack spacing={1}>
        {/* AI filter input — hidden where the grounded AI backend isn't wired
            for this source (pass showAiFilter={false}); Basic/Query stay. */}
        {!basicOnly && showAiFilter && (
          <TextField
            size="small"
            placeholder={aiLoading ? "Parsing with AI..." : aiPlaceholder}
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
              sx: { fontSize: 12, height: 32 },
            }}
            fullWidth
          />
        )}
        {!basicOnly && showAiFilter && aiError && (
          <Typography
            variant="caption"
            sx={{ fontSize: 11, color: "text.secondary", px: 0.5 }}
          >
            AI unavailable, using local parser
          </Typography>
        )}

        {/* Tabs */}
        {!basicOnly && (
          <Tabs
            value={activeTab}
            onChange={(_, v) => setActiveTab(v)}
            sx={{
              boxShadow: "none",
              minHeight: 28,
              borderBottom: "1px solid",
              borderColor: "divider",
              "& .MuiTab-root": {
                minHeight: 28,
                py: 0.5,
                px: 1,
                textTransform: "none",
                fontWeight: 500,
                minWidth: 0,
              },
            }}
          >
            <Tab value="basic" label="Basic" />
            <Tab value="query" label="Query" />
          </Tabs>
        )}

        {activeTab === "basic" ? (
          <>
            {!basicOnly && (
              <Typography
                variant="caption"
                sx={{
                  color: "text.secondary",
                  fontSize: 11,
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  px: 0.5,
                }}
              >
                Basic Filter
              </Typography>
            )}
            {filterFields.length === 0 ? (
              <Typography
                sx={{ color: "text.secondary", fontSize: 12, px: 0.5, py: 0.5 }}
              >
                No filters available for this view.
              </Typography>
            ) : (
              <Stack spacing={0.75}>
                {rows.map((row, i) => (
                  <FilterRow
                    key={i}
                    filter={row}
                    index={i}
                    filterFields={filterFields}
                    fieldMap={fieldMap}
                    onChange={handleUpdateRow}
                    onRemove={handleRemoveRow}
                    takenSingleFields={takenSingleFields}
                  />
                ))}
              </Stack>
            )}
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
            >
              <Button
                size="small"
                startIcon={<Iconify icon="mingcute:add-line" width={14} />}
                onClick={handleAddRow}
                disabled={!nextAvailableField}
                sx={{ textTransform: "none", fontSize: 12, fontWeight: 500 }}
              >
                Add filter
              </Button>
              {activeFilterCount > 0 && (
                <Button
                  size="small"
                  onClick={handleClear}
                  sx={{
                    textTransform: "none",
                    fontSize: 12,
                    color: "text.secondary",
                  }}
                >
                  Clear all
                </Button>
              )}
            </Stack>
          </>
        ) : (
          <>
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                fontSize: 11,
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                px: 0.5,
              }}
            >
              Query Builder
            </Typography>
            <QueryInput
              filterFields={filterFields}
              fieldMap={fieldMap}
              onApply={handleApplyFromNlp}
              initialTokens={rows.filter((r) => {
                if (Array.isArray(r?.value)) return r.value.length > 0;
                return Boolean(r?.value);
              })}
            />
            <Typography
              variant="caption"
              sx={{ fontSize: 10, color: "text.disabled", px: 0.5 }}
            >
              Type property → pick operator → pick/type value. Backspace to
              undo. Click chip to edit.
            </Typography>
          </>
        )}
      </Stack>
    </Popover>
  );
};

FilterPanel.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  filterFields: PropTypes.arrayOf(
    PropTypes.shape({
      value: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      type: PropTypes.oneOf(["string", "enum"]).isRequired,
      choices: PropTypes.arrayOf(PropTypes.string),
      // {choiceValue: humanLabel} — when set, the UI shows only the label.
      choiceLabels: PropTypes.object,
    }),
  ).isRequired,
  currentFilters: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
  onApply: PropTypes.func.isRequired,
  aiPlaceholder: PropTypes.string,
  showAiFilter: PropTypes.bool,
  width: PropTypes.number,
  projectId: PropTypes.string,
  source: PropTypes.string,
  basicOnly: PropTypes.bool,
  placement: PropTypes.oneOf(["bottom-start", "bottom-end"]),
};

export { QueryInput };
export default FilterPanel;
