import {
  Box,
  Checkbox,
  InputAdornment,
  Popover,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  useSortable,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Iconify from "src/components/iconify";
import { useDebounce } from "src/hooks/use-debounce";
import { groupEvalColumnsByTask } from "src/sections/projects/LLMTracing/evalTaskMock";

// ---------------------------------------------------------------------------
// Aggregate selection state
// ---------------------------------------------------------------------------
const SELECTION_STATE = Object.freeze({
  NONE: "none",
  SOME: "some",
  ALL: "all",
});

const aggregateState = (cols) => {
  if (!cols || cols.length === 0) return SELECTION_STATE.NONE;
  const checkedCount = cols.filter((c) => c.isVisible).length;
  if (checkedCount === 0) return SELECTION_STATE.NONE;
  if (checkedCount === cols.length) return SELECTION_STATE.ALL;
  return SELECTION_STATE.SOME;
};

const toggleMap = (cols, value) =>
  (cols || []).reduce((acc, c) => {
    acc[c.id] = value;
    return acc;
  }, {});

// ---------------------------------------------------------------------------
// Bulk-select row (top-level "Select all")
// ---------------------------------------------------------------------------
const BulkSelectRow = ({ label, state, onToggle }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      gap: "4px",
      px: "4px",
      py: "2px",
      borderRadius: "4px",
      cursor: "pointer",
      "&:hover": { bgcolor: "action.hover" },
    }}
    onClick={() => onToggle(state !== SELECTION_STATE.ALL)}
  >
    <Checkbox
      size="small"
      checked={state === SELECTION_STATE.ALL}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => onToggle(e.target.checked)}
      sx={{
        p: 0,
        width: 16,
        height: 16,
        "& .MuiSvgIcon-root": { fontSize: 16 },
        "&.Mui-checked": { color: "primary.light" },
      }}
      inputProps={{ "aria-label": `Toggle ${label}` }}
    />
    <Typography
      variant="body2"
      noWrap
      sx={{
        fontSize: 14,
        lineHeight: "22px",
        color: "text.primary",
        flex: 1,
        minWidth: 0,
      }}
    >
      {label}
    </Typography>
  </Box>
);

BulkSelectRow.propTypes = {
  label: PropTypes.string.isRequired,
  state: PropTypes.oneOf([
    SELECTION_STATE.NONE,
    SELECTION_STATE.SOME,
    SELECTION_STATE.ALL,
  ]).isRequired,
  onToggle: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Draggable column row
// ---------------------------------------------------------------------------
const DraggableColumnRow = ({ id, name, checked, onChange }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : 1,
  };

  return (
    <Box
      ref={setNodeRef}
      style={style}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: "4px",
        px: "4px",
        py: "2px",
        borderRadius: "4px",
        cursor: "default",
        "&:hover": { bgcolor: "action.hover" },
        opacity: isDragging ? 0.6 : 1,
      }}
    >
      <Checkbox
        size="small"
        checked={checked}
        onChange={onChange}
        sx={{
          p: 0,
          width: 16,
          height: 16,
          "& .MuiSvgIcon-root": { fontSize: 16 },
          "&.Mui-checked": { color: "primary.light" },
        }}
      />
      <Box
        {...attributes}
        {...listeners}
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 16,
          height: 16,
          cursor: "grab",
          color: "text.disabled",
          "&:active": { cursor: "grabbing" },
          flexShrink: 0,
        }}
      >
        <Iconify icon="mdi:dots-grid" width={14} />
      </Box>
      <Typography
        variant="body2"
        noWrap
        sx={{
          fontSize: 14,
          lineHeight: "22px",
          color: "text.primary",
          flex: 1,
          minWidth: 0,
        }}
      >
        {name}
      </Typography>
    </Box>
  );
};

DraggableColumnRow.propTypes = {
  id: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  checked: PropTypes.bool,
  onChange: PropTypes.func,
};

// ---------------------------------------------------------------------------
// Eval Task group section (§4.2) — master toggle (with indeterminate) over the
// group's evals, plus a per-eval toggle for each. Collapsible. Used only when
// `useGrouping` is set and eval columns are present.
// ---------------------------------------------------------------------------
const EvalTaskGroupSection = ({ group, onColumnChange }) => {
  const [collapsed, setCollapsed] = useState(false);
  const state = aggregateState(group.evals);
  return (
    <Box>
      {/* Group master row */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "4px",
          px: "4px",
          py: "2px",
          borderRadius: "4px",
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
        onClick={() => setCollapsed((c) => !c)}
      >
        <Checkbox
          size="small"
          checked={state === SELECTION_STATE.ALL}
          indeterminate={state === SELECTION_STATE.SOME}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) =>
            onColumnChange(toggleMap(group.evals, e.target.checked))
          }
          sx={{
            p: 0,
            width: 16,
            height: 16,
            "& .MuiSvgIcon-root": { fontSize: 16 },
            "&.Mui-checked": { color: "primary.light" },
            "&.MuiCheckbox-indeterminate": { color: "primary.light" },
          }}
          inputProps={{ "aria-label": `Toggle ${group.taskName} task` }}
        />
        <Typography
          noWrap
          sx={{
            flex: 1,
            minWidth: 0,
            fontSize: 12,
            fontWeight: 600,
            letterSpacing: "0.02em",
            color: "text.secondary",
          }}
        >
          {group.taskName}
        </Typography>
        <Iconify
          icon={collapsed ? "mdi:chevron-down" : "mdi:chevron-up"}
          width={16}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
      </Box>

      {/* Per-eval rows */}
      {!collapsed &&
        group.evals.map((col) => (
          <Box
            key={col.id}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: "4px",
              pl: "20px",
              pr: "4px",
              py: "2px",
              borderRadius: "4px",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Checkbox
              size="small"
              checked={col.isVisible}
              onChange={(e) => onColumnChange({ [col.id]: e.target.checked })}
              sx={{
                p: 0,
                width: 16,
                height: 16,
                "& .MuiSvgIcon-root": { fontSize: 16 },
                "&.Mui-checked": { color: "primary.light" },
              }}
            />
            <Typography
              noWrap
              sx={{
                flex: 1,
                minWidth: 0,
                fontSize: 14,
                lineHeight: "22px",
                color: "text.primary",
              }}
            >
              {col.name}
            </Typography>
          </Box>
        ))}
    </Box>
  );
};

EvalTaskGroupSection.propTypes = {
  group: PropTypes.object.isRequired,
  onColumnChange: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// ColumnConfigureDropDown
// ---------------------------------------------------------------------------
const ColumnConfigureDropDown = ({
  open,
  onClose,
  anchorEl,
  columns,
  setColumns,
  onColumnVisibilityChange,
  defaultGrouping: _defaultGrouping = "Run Columns",
  useGrouping = false,
  placement = "bottom",
}) => {
  const theme = useTheme();
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 300);

  // Flatten columns for display (no accordion grouping)
  const flatColumns = useMemo(() => {
    if (!columns) return [];
    return columns;
  }, [columns]);

  const filteredColumns = useMemo(() => {
    if (!debouncedSearchQuery) return flatColumns;
    return flatColumns.filter((col) =>
      col?.name?.toLowerCase()?.includes(debouncedSearchQuery.toLowerCase()),
    );
  }, [flatColumns, debouncedSearchQuery]);

  // §4.2 — when grouping is enabled, eval columns are split out and rendered as
  // collapsible Task-group sections (below). Base columns keep the existing
  // flat, draggable list. When grouping is off, behaviour is unchanged.
  const baseColumns = useMemo(
    () =>
      useGrouping
        ? filteredColumns.filter((c) => c?.groupBy !== "Evaluation Metrics")
        : filteredColumns,
    [filteredColumns, useGrouping],
  );
  const evalTaskGroups = useMemo(() => {
    if (!useGrouping) return [];
    const evalCols = filteredColumns.filter(
      (c) => c?.groupBy === "Evaluation Metrics",
    );
    return groupEvalColumnsByTask(evalCols);
  }, [filteredColumns, useGrouping]);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const onColumnChange = (updateObj) => {
    const data = (columns || []).reduce((acc, c) => {
      acc[c.id] = c.isVisible;
      return acc;
    }, {});

    Object.entries(updateObj).forEach(([id, value]) => {
      data[id] = value;
    });

    onColumnVisibilityChange(data);
  };

  function handleDragEnd(event) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = columns.findIndex((item) => item.id === active.id);
    const newIndex = columns.findIndex((item) => item.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;

    const newColumns = arrayMove(columns, oldIndex, newIndex);
    setColumns(newColumns);
  }

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorEl={anchorEl}
      anchorOrigin={
        placement === "right"
          ? { vertical: "top", horizontal: "right" }
          : { vertical: "bottom", horizontal: "right" }
      }
      transformOrigin={
        placement === "right"
          ? { vertical: "top", horizontal: "left" }
          : { vertical: -14, horizontal: "right" }
      }
      slotProps={{
        paper: {
          sx: {
            width: placement === "right" ? 220 : 260,
            maxHeight: 400,
            ...(placement === "right" && { ml: 0.5 }),
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: "4px",
            boxShadow: "1px 1px 12px 10px rgba(0,0,0,0.04)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          },
        },
      }}
    >
      {/* Search */}
      <Box sx={{ p: 1, flexShrink: 0 }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Search"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
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
          }}
          sx={{
            "& .MuiOutlinedInput-root": {
              height: 28,
              fontSize: 14,
              "& input": {
                py: 0,
                px: 0.5,
              },
              "& fieldset": {
                borderColor: "divider",
              },
              "&:hover fieldset": {
                borderColor: "text.disabled",
              },
              "&.Mui-focused fieldset": {
                borderColor: "text.secondary",
                borderWidth: 1,
              },
            },
          }}
        />
      </Box>

      {/* Select-all header (scoped to current search results) */}
      {filteredColumns.length > 0 && (
        <Box
          sx={{
            px: 0.5,
            pb: 0.5,
            flexShrink: 0,
            borderBottom: `1px solid ${theme.palette.divider}`,
          }}
        >
          <BulkSelectRow
            label="Select all"
            state={aggregateState(filteredColumns)}
            onToggle={(value) =>
              onColumnChange(toggleMap(filteredColumns, value))
            }
          />
        </Box>
      )}

      {/* Column list */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          px: 0.5,
          pb: 1,
          display: "flex",
          flexDirection: "column",
          gap: "2px",
        }}
      >
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={baseColumns.map((c) => c.id)}
            strategy={verticalListSortingStrategy}
          >
            {baseColumns.map((column) => (
              <DraggableColumnRow
                key={column.id}
                id={column.id}
                name={column.name}
                checked={column.isVisible}
                onChange={(e) => {
                  onColumnChange({ [column.id]: e.target.checked });
                }}
              />
            ))}
          </SortableContext>
        </DndContext>

        {/* §4.2 — eval columns grouped by parent Task */}
        {evalTaskGroups.map((group) => (
          <EvalTaskGroupSection
            key={group.taskId}
            group={group}
            onColumnChange={onColumnChange}
          />
        ))}

        {filteredColumns.length === 0 && (
          <Typography
            variant="body2"
            sx={{
              color: "text.disabled",
              fontSize: 13,
              textAlign: "center",
              py: 2,
            }}
          >
            No columns found
          </Typography>
        )}
      </Box>
    </Popover>
  );
};

ColumnConfigureDropDown.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchorEl: PropTypes.object,
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  onColumnVisibilityChange: PropTypes.func,
  defaultGrouping: PropTypes.string,
  useGrouping: PropTypes.bool,
  placement: PropTypes.oneOf(["bottom", "right"]),
};

export default ColumnConfigureDropDown;
