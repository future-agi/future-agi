import React, { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import Iconify from "src/components/iconify";
import { getRandomId } from "src/utils/utils";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";

// ── Operator maps (new panel format ↔ old task filter format) ──
const OP_NEW_TO_OLD = {
  is: "equals",
  is_not: "not_equals",
  contains: "contains",
  not_contains: "not_contains",
  equal_to: "equal_to",
  not_equal_to: "not_equal_to",
  greater_than: "greater_than",
  greater_than_or_equal: "greater_than_or_equal",
  less_than: "less_than",
  less_than_or_equal: "less_than_or_equal",
  between: "between",
  not_between: "not_between",
};

const OP_OLD_TO_NEW = {
  equals: "is",
  not_equals: "is_not",
  contains: "contains",
  not_contains: "not_contains",
  is: "is",
  is_not: "is_not",
  equal_to: "equal_to",
  not_equal_to: "not_equal_to",
  greater_than: "greater_than",
  greater_than_or_equal: "greater_than_or_equal",
  less_than: "less_than",
  less_than_or_equal: "less_than_or_equal",
  between: "between",
  not_between: "not_between",
};

const OP_DISPLAY = {
  is: "is",
  is_not: "is not",
  contains: "contains",
  not_contains: "not contains",
  equal_to: "=",
  not_equal_to: "≠",
  greater_than: ">",
  greater_than_or_equal: "≥",
  less_than: "<",
  less_than_or_equal: "≤",
  between: "between",
  not_between: "not between",
};

// ── new panel filter → old task form filter(s) ──
// Each picked value becomes a separate row so the backend's
// `getNewTaskFilters` pushes them into the same property array.
// We stash `fieldCategory` and `fieldLabel` on each row so the live
// preview (which reads raw useWatch values) can reconstruct the
// tracing API array without losing metadata. Zod strips these at
// submit time so the backend payload is unaffected.
function convertNewToOld(newFilters) {
  const out = [];
  (newFilters || []).forEach((f) => {
    if (!f?.field) return;
    const values = Array.isArray(f.value) ? f.value : [f.value];
    const isAttribute = f.fieldCategory === "attribute";
    values.forEach((v) => {
      if (v === undefined || v === null || v === "") return;
      out.push({
        id: getRandomId(),
        property: isAttribute ? "attributes" : f.field,
        propertyId: f.field,
        fieldCategory: f.fieldCategory || "system",
        fieldLabel: f.fieldLabel || f.field,
        filterConfig: {
          filterType: f.fieldType === "number" ? "number" : "text",
          filterOp: OP_NEW_TO_OLD[f.operator] || f.operator || "equals",
          filterValue: v,
        },
      });
    });
  });
  return out;
}

// ── old task form filter → new panel format (one row per property+op group) ──
function convertOldToNew(oldFilters) {
  const groups = new Map();
  (oldFilters || []).forEach((f) => {
    if (!f) return;
    const isAttribute = f.property === "attributes";
    const field = isAttribute ? f.propertyId : f.property;
    if (!field) return;
    const op = f?.filterConfig?.filterOp || "equals";
    const category = f.fieldCategory || (isAttribute ? "attribute" : "system");
    const key = `${field}|${op}|${category}`;
    if (!groups.has(key)) {
      groups.set(key, {
        field,
        fieldLabel: f.fieldLabel || field,
        fieldType:
          f?.filterConfig?.filterType === "number" ? "number" : "string",
        fieldCategory: category,
        operator: OP_OLD_TO_NEW[op] || op,
        value: [],
      });
    }
    const val = f?.filterConfig?.filterValue;
    if (val !== undefined && val !== null && val !== "") {
      groups.get(key).value.push(val);
    }
  });
  return Array.from(groups.values());
}

// ── Chip display for an active filter ──
const FilterChip = ({ filter, onRemove }) => {
  const opLabel = OP_DISPLAY[filter.operator] || filter.operator || "is";
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
        {filter.fieldLabel || filter.field}
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
