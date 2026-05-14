import React, { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { getRandomId } from "src/utils/utils";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";

// ── Operator handling — canonical backend ops ──
//
// `TraceFilterPanel` (PR #432 / TH-4924) emits canonical backend op names
// directly: `equals`, `not_equals`, `in`, `not_in`, `contains`,
// `not_contains`, `starts_with`, `ends_with`, `is_null`, `is_not_null`,
// `greater_than`, `greater_than_or_equal`, `less_than`,
// `less_than_or_equal`, `between`, `not_between`. The thumbs / categorical
// / id-only dropdowns inside the panel still emit legacy `is`/`is_not` —
// alias those to canonical so the wire is consistent.
const LEGACY_OP_ALIAS = { is: "equals", is_not: "not_equals" };

const RANGE_OPS = new Set(["between", "not_between"]);
const LIST_OPS = new Set(["in", "not_in"]);
const NO_VALUE_OPS = new Set(["is_null", "is_not_null"]);

// Legacy string ops persisted before TH-4924 land in form state as
// `equals`/`not_equals`. Rewrite to `in`/`not_in` on read so the new
// panel renders the row under the multi-value picker.
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
  // legacy (still rendered if a stale row survives until the next save)
  is: "is",
  is_not: "is not",
  equal_to: "=",
  not_equal_to: "≠",
};

// ── new panel filter → form filter(s) ──
//
// List ops (`in`/`not_in`) carry the full array on a single form row so
// the wire keeps the BE's canonical shape; range ops carry a 2-element
// array; no-value ops omit `filterValue`; other ops explode into one
// scalar row per value so system-filter accumulation keeps working.
// `fieldCategory` / `fieldLabel` are stashed for the live preview and
// chip rendering — zod strips them on submit.
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
          : fieldType === "thumbs"
            ? "thumbs"
            : fieldType === "categorical"
              ? "categorical"
              : "text";
    const op = LEGACY_OP_ALIAS[f.operator] || f.operator || "equals";

    const base = {
      property: isAttribute ? "attributes" : f.field,
      propertyId: f.field,
      fieldCategory: f.fieldCategory || "system",
      // Panel rows store the display name under `fieldName` (set by
      // PropertyPicker → handlePropertySelect). Fall back to `fieldLabel`
      // for any caller still using the older key, then the raw id.
      fieldLabel: f.fieldName || f.fieldLabel || f.field,
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

// ── form filter → new panel format (one row per property+op group) ──
function convertOldToNew(oldFilters) {
  const groups = new Map();
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

    // Aliased legacy → canonical first, then string-specific rehydration.
    let op = LEGACY_OP_ALIAS[rawOp] || rawOp;
    if (isStringLike(fieldType) && HYDRATE_STRING_OP[op]) {
      op = HYDRATE_STRING_OP[op];
    }

    const key = `${field}|${op}|${category}`;
    if (!groups.has(key)) {
      groups.set(key, {
        field,
        fieldLabel: f.fieldLabel || field,
        fieldType,
        fieldCategory: category,
        operator: op,
        value: [],
      });
    }

    if (NO_VALUE_OPS.has(op)) return;

    const val = f?.filterConfig?.filterValue;
    if (RANGE_OPS.has(op)) {
      // Range: the form row carries the [low, high] array directly.
      groups.get(key).value = Array.isArray(val) ? val : [];
      return;
    }
    if (val === undefined || val === null || val === "") return;
    if (Array.isArray(val)) {
      groups.get(key).value.push(...val);
    } else {
      groups.get(key).value.push(val);
    }
  });
  return Array.from(groups.values());
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

// Map task rowType → TraceFilterPanel `tab` so the property picker
// surfaces Trace ID / Span ID the same way LLM Tracing does. Callers
// use inconsistent casing ("spans"/"Span", "traces"/"Trace") so we
// normalize. Sessions / voiceCalls return null (no id fields).
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
  const addBtnRef = useRef(null);

  // Re-anchor when chips swap the trigger DOM node, so an open popover
  // doesn't end up attached to a detached element.
  const hasFiltersForEffect = panelFilters.length > 0;
  useEffect(() => {
    if (anchorEl && addBtnRef.current && anchorEl !== addBtnRef.current) {
      setAnchorEl(addBtnRef.current);
    }
  }, [hasFiltersForEffect, anchorEl]);

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

  const openPanel = useCallback((e) => {
    setAnchorEl(e?.currentTarget || addBtnRef.current);
  }, []);

  const hasFilters = panelFilters.length > 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {hasFilters ? (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.75,
            flexWrap: "wrap",
          }}
        >
          {panelFilters.map((f, idx) => (
            <FilterChip
              key={`${f.field}-${idx}`}
              filter={f}
              onRemove={() => handleRemove(idx)}
            />
          ))}

          {/* + button to add another filter */}
          <Box
            ref={addBtnRef}
            component="button"
            type="button"
            onClick={openPanel}
            sx={(theme) => ({
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 26,
              height: 26,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              bgcolor:
                theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.04)"
                  : "background.paper",
              color: "text.secondary",
              cursor: "pointer",
              p: 0,
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
          ref={addBtnRef}
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
