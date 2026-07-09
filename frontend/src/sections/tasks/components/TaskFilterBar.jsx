import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { getRandomId } from "src/utils/utils";
import TraceFilterPanel, {
  useTraceFilterProperties,
} from "src/sections/projects/LLMTracing/TraceFilterPanel";
import {
  FIELD_CATEGORY_TO_COL_TYPE,
  RANGE_OPS,
  LIST_OPS,
  NO_VALUE_OPS,
} from "src/sections/common/EvalsTasks/common";
import { useDashboardFilterValues } from "src/hooks/useDashboards";
import {
  getPickerOptionValue,
  getPickerOptionLabel,
  getPickerOptionSecondaryLabel,
} from "src/sections/projects/LLMTracing/filterValuePickerUtils";

// ── Operator handling — canonical backend ops ──
//
// `TraceFilterPanel` (PR #432 / TH-4924) emits canonical backend op names
// directly: `equals`, `not_equals`, `in`, `not_in`, `contains`,
// `not_contains`, `starts_with`, `ends_with`, `is_null`, `is_not_null`,
// `greater_than`, `greater_than_or_equal`, `less_than`,
// `less_than_or_equal`, `between`, `not_between`.
const resolveApiColType = (apiColType, fieldCategory) =>
  apiColType || FIELD_CATEGORY_TO_COL_TYPE[fieldCategory] || "SPAN_ATTRIBUTE";

// Legacy `equals`/`not_equals` on string rows → multi-value `in`/`not_in`
// on hydration so the new panel renders the multi-select picker.
const HYDRATE_STRING_OP = { equals: "in", not_equals: "not_in" };

const isStringLike = (fieldType) =>
  fieldType === "text" || fieldType === "string";

const coerceForType = (val, fieldType) => {
  if (val === null || val === undefined || val === "") return val;
  if (Array.isArray(val)) return val.map((v) => coerceForType(v, fieldType));
  if (fieldType === "number") {
    const n = Number(val);
    return Number.isNaN(n) ? val : n;
  }
  if (fieldType === "boolean") {
    if (val === true || val === false) return val;
    if (val === "true") return true;
    if (val === "false") return false;
  }
  return val;
};

const OP_DISPLAY = {
  // canonical
  equals: "equals",
  not_equals: "not equals",
  in: "is one of",
  not_in: "is not one of",
  contains: "contains",
  not_contains: "not contains",
  starts_with: "starts with",
  ends_with: "ends with",
  is_null: "is null",
  is_not_null: "is not null",
  greater_than: ">",
  greater_than_or_equal: "≥",
  less_than: "<",
  less_than_or_equal: "≤",
  between: "between",
  not_between: "not between",
};

// Panel filter → form row(s). List/range ops keep array `filterValue`;
// no-value ops drop it; other ops explode into one scalar row per value.
function convertNewToOld(newFilters) {
  const out = [];
  (newFilters || []).forEach((f) => {
    if (!f?.field) return;
    const isAttribute = f.fieldCategory === "attribute";
    const fieldType = f.fieldType || "string";
    const filterType =
      fieldType === "number"
        ? "number"
        : fieldType === "boolean"
          ? "boolean"
          : "text";
    const op = f.operator || "equals";

    const base = {
      property: isAttribute ? "attributes" : f.field,
      propertyId: f.field,
      fieldCategory: f.fieldCategory || "system",
      fieldLabel: f.fieldName || f.fieldLabel || f.field,
      apiColType: resolveApiColType(f.apiColType, f.fieldCategory),
    };

    if (NO_VALUE_OPS.has(op)) {
      out.push({
        id: getRandomId(),
        ...base,
        filterConfig: { filterType, filterOp: op },
      });
      return;
    }

    if (RANGE_OPS.has(op)) {
      const arr = Array.isArray(f.value) ? f.value : [];
      if (arr.length < 2) return;
      out.push({
        id: getRandomId(),
        ...base,
        filterConfig: {
          filterType,
          filterOp: op,
          filterValue: coerceForType(arr.slice(0, 2), fieldType),
        },
      });
      return;
    }

    if (LIST_OPS.has(op)) {
      const arr = (Array.isArray(f.value) ? f.value : [f.value]).filter(
        (v) => v !== undefined && v !== null && v !== "",
      );
      if (arr.length === 0) return;
      out.push({
        id: getRandomId(),
        ...base,
        filterConfig: {
          filterType,
          filterOp: op,
          filterValue: coerceForType(arr, fieldType),
        },
      });
      return;
    }

    // Single-value ops: explode any incoming array (legacy multi-value
    // `equals` from saved tasks) into one scalar row per value.
    const arr = Array.isArray(f.value) ? f.value : [f.value];
    arr.forEach((v) => {
      if (v === undefined || v === null || v === "") return;
      out.push({
        id: getRandomId(),
        ...base,
        filterConfig: {
          filterType,
          filterOp: op,
          filterValue: coerceForType(v, fieldType),
        },
      });
    });
  });
  return out;
}

// ── form filter → new panel format ──
// One form row → one panel row. Only ops with a natural multi-value shape
// (range, list, or string equals/not_equals just rewritten to in/not_in via
// HYDRATE_STRING_OP) collapse same-(field, op) rows into one multi-value
// panel row — grouping any other op (e.g. not_contains) would let the chip
// + panel UI fold "exclude A AND exclude B" into a single "[A, B]" row.
function convertOldToNew(oldFilters) {
  const groups = new Map();
  const result = [];
  (oldFilters || []).forEach((f) => {
    if (!f) return;
    const isAttribute = f.property === "attributes";
    const field = isAttribute ? f.propertyId : f.property;
    if (!field) return;

    const rawOp = f?.filterConfig?.filterOp || "equals";
    const category = f.fieldCategory || (isAttribute ? "attribute" : "system");
    const ft = f?.filterConfig?.filterType;
    const fieldType =
      ft === "number" ? "number" : ft === "boolean" ? "boolean" : "string";

    let op = rawOp;
    const hydrated = isStringLike(fieldType) && HYDRATE_STRING_OP[op];
    if (hydrated) {
      op = HYDRATE_STRING_OP[op];
    }

    const isMultiValueOp =
      RANGE_OPS.has(op) || LIST_OPS.has(op) || Boolean(hydrated);

    let entry;
    if (isMultiValueOp) {
      const key = `${field}|${op}|${category}`;
      entry = groups.get(key);
      if (!entry) {
        entry = {
          field,
          fieldLabel: f.fieldLabel || field,
          fieldType,
          fieldCategory: category,
          operator: op,
          value: [],
        };
        groups.set(key, entry);
        result.push(entry);
      }
    } else {
      entry = {
        field,
        fieldLabel: f.fieldLabel || field,
        fieldType,
        fieldCategory: category,
        // Preserved so the panel re-renders the right chip on edit-open.
        apiColType: resolveApiColType(
          f.apiColType || f?.filterConfig?.colType,
          category,
        ),
        operator: op,
        value: [],
      };
      result.push(entry);
    }

    if (NO_VALUE_OPS.has(op)) return;

    const val = f?.filterConfig?.filterValue;
    if (RANGE_OPS.has(op)) {
      entry.value = Array.isArray(val) ? val : [];
      return;
    }
    if (val === undefined || val === null || val === "") return;
    if (Array.isArray(val)) {
      entry.value.push(...val);
    } else {
      entry.value.push(val);
    }
  });
  return result;
}

// ── Chip display for an active filter ──
const FilterChip = ({ filter, onRemove }) => {
  const opLabel = OP_DISPLAY[filter.operator] || filter.operator || "equals";
  const valueStr = Array.isArray(filter.value)
    ? filter.value.join(", ")
    : String(filter.value ?? "");

  return (
    <Box
      sx={(theme) => ({
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        px: 0.75,
        py: 0.25,
        bgcolor:
          theme.palette.mode === "dark"
            ? "rgba(255,255,255,0.06)"
            : "rgba(0,0,0,0.04)",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        fontSize: 11,
        color: "text.primary",
        whiteSpace: "nowrap",
        minHeight: 26,
      })}
    >
      <Iconify
        icon="mdi:filter-variant"
        width={12}
        sx={{ color: "text.disabled" }}
      />
      <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
        {filter.fieldName || filter.fieldLabel || filter.field}
      </Typography>
      <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
        {opLabel}
      </Typography>
      <Typography
        sx={{
          fontSize: 12,
          fontWeight: 600,
          color: "text.primary",
          maxWidth: 180,
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {valueStr}
      </Typography>
      <Iconify
        icon="mdi:close"
        width={12}
        sx={{
          cursor: "pointer",
          color: "text.disabled",
          ml: 0.25,
          "&:hover": { color: "text.primary" },
        }}
        onClick={onRemove}
      />
    </Box>
  );
};

FilterChip.propTypes = {
  filter: PropTypes.object.isRequired,
  onRemove: PropTypes.func.isRequired,
};

// Task rowType → TraceFilterPanel `tab`. Sessions / voiceCalls have no
// id-field tab. Casing is normalized to handle inconsistent callers.
const rowTypeToFilterTab = (rowType) => {
  const key = String(rowType || "").toLowerCase();
  if (key === "spans" || key === "span") return "spans";
  if (key === "traces" || key === "trace") return "trace";
  return null;
};

// ── Main ──
const TaskFilterBar = ({
  control,
  setValue,
  projectId,
  isSimulator = false,
  rowType,
}) => {
  // Read the form filters (old format) and mirror them in local state (new format).
  const formFilters = useWatch({ control, name: "filters" });
  const [panelFilters, setPanelFilters] = useState(() =>
    convertOldToNew(formFilters),
  );
  const suppressNextSync = useRef(false);

  // Resolve UUID column ids → display names. Shares cache with TraceFilterPanel.
  const { data: properties = [] } = useTraceFilterProperties(projectId, {
    isSimulator,
  });
  const propertyById = useMemo(() => {
    const map = {};
    for (const p of properties) map[p.id] = p;
    return map;
  }, [properties]);

  // Resolve annotator user UUIDs → "Name (email)" for chip values.
  const hasAnnotatorFilter = panelFilters.some((f) => f.field === "annotator");
  const { data: annotatorOptions = [] } = useDashboardFilterValues({
    metricName: "annotator",
    metricType: "annotation_metric",
    projectIds: projectId ? [projectId] : [],
    source: "traces",
    enabled: hasAnnotatorFilter,
  });
  const annotatorLabelById = useMemo(() => {
    const map = {};
    for (const opt of annotatorOptions) {
      const value = String(getPickerOptionValue(opt));
      if (!value) continue;
      const label = getPickerOptionLabel(opt);
      const email = getPickerOptionSecondaryLabel(opt);
      map[value] = email ? `${label} (${email})` : label;
    }
    return map;
  }, [annotatorOptions]);

  const enrichedFilters = useMemo(
    () =>
      panelFilters.map((f) => {
        const prop = propertyById[f.field];
        let next = f;
        if (!f.fieldName && f.fieldLabel === f.field && prop) {
          next = { ...next, fieldLabel: prop.name };
        }
        if (f.field === "annotator" && Object.keys(annotatorLabelById).length) {
          const remap = (v) => annotatorLabelById[String(v)] || v;
          next = {
            ...next,
            value: Array.isArray(f.value) ? f.value.map(remap) : remap(f.value),
          };
        }
        return next;
      }),
    [panelFilters, propertyById, annotatorLabelById],
  );

  // Keep local panel state in sync with form filters when they change externally
  // (e.g. edit mode hydration). Skip the sync right after our own apply.
  useEffect(() => {
    if (suppressNextSync.current) {
      suppressNextSync.current = false;
      return;
    }
    setPanelFilters(convertOldToNew(formFilters));
  }, [formFilters]);

  const [anchorEl, setAnchorEl] = useState(null);
  // Anchor the panel to the filter bar row (not the "+", which drifts as chips
  // wrap). It opens flush below the bar and stays glued there as filters are
  // added/removed — avoiding the value-dropdown float, the "chases the +"
  // drift, and the stranded panel on delete (TH-6534).
  const barRef = useRef(null);

  const applyPanelFilters = useCallback(
    (next) => {
      setPanelFilters(next || []);
      suppressNextSync.current = true;
      setValue("filters", convertNewToOld(next), {
        shouldDirty: true,
        shouldValidate: false,
      });
    },
    [setValue],
  );

  const handleRemove = useCallback(
    (idx) => {
      const next = panelFilters.filter((_, i) => i !== idx);
      applyPanelFilters(next);
    },
    [panelFilters, applyPanelFilters],
  );

  const handleClear = useCallback(() => {
    applyPanelFilters([]);
  }, [applyPanelFilters]);

  const anchorToBar = useCallback(
    () => ({
      nodeType: 1,
      getBoundingClientRect: () => barRef.current?.getBoundingClientRect(),
    }),
    [],
  );

  const openPanel = useCallback(() => {
    if (!barRef.current) return;
    setAnchorEl(anchorToBar());
  }, [anchorToBar]);

  // Keep the panel glued just below the bar as chips wrap/unwrap while it's
  // open: a size change swaps in a fresh anchor object so MUI re-positions.
  const isPanelOpen = Boolean(anchorEl);
  useEffect(() => {
    const el = barRef.current;
    if (!isPanelOpen || !el) return undefined;
    const ro = new ResizeObserver(() => setAnchorEl(anchorToBar()));
    ro.observe(el);
    return () => ro.disconnect();
  }, [isPanelOpen, anchorToBar]);

  const hasFilters = panelFilters.length > 0;

  return (
    <Box ref={barRef} sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {hasFilters ? (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            flexWrap: "wrap",
          }}
        >
          {enrichedFilters.map((f, idx) => (
            <FilterChip
              key={`${f.field}-${idx}`}
              filter={f}
              onRemove={() => handleRemove(idx)}
            />
          ))}

          <Box
            component="button"
            type="button"
            onClick={openPanel}
            sx={(theme) => ({
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 26,
              height: 26,
              p: 0,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              bgcolor:
                theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.04)"
                  : "background.paper",
              color: "text.secondary",
              cursor: "pointer",
              "&:hover": {
                color: "text.primary",
                bgcolor:
                  theme.palette.mode === "dark"
                    ? "rgba(255,255,255,0.08)"
                    : "action.hover",
                borderColor: "text.disabled",
              },
            })}
          >
            <Iconify icon="mdi:plus" width={14} />
          </Box>

          <Box sx={{ flex: 1 }} />
          <Button
            size="small"
            onClick={handleClear}
            sx={{
              textTransform: "none",
              fontSize: 12,
              color: "text.secondary",
              minWidth: "auto",
              p: 0,
              "&:hover": { color: "text.primary", bgcolor: "transparent" },
            }}
          >
            Clear
          </Button>
        </Box>
      ) : (
        <Button
          onClick={openPanel}
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="mdi:filter-variant" width={14} />}
          sx={{
            textTransform: "none",
            fontWeight: 500,
            fontSize: "12px",
            height: 30,
            width: "fit-content",
            borderColor: "divider",
            color: "text.secondary",
            "&:hover": {
              borderColor: "text.disabled",
              bgcolor: "action.hover",
              color: "text.primary",
            },
          }}
        >
          Add filter
        </Button>
      )}

      <TraceFilterPanel
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={() => setAnchorEl(null)}
        currentFilters={panelFilters}
        projectId={projectId}
        isSimulator={isSimulator}
        tab={rowTypeToFilterTab(rowType)}
        onApply={(next) => applyPanelFilters(next || [])}
      />
    </Box>
  );
};

TaskFilterBar.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  projectId: PropTypes.string,
  isSimulator: PropTypes.bool,
  rowType: PropTypes.string,
};

export default TaskFilterBar;
