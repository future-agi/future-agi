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
import TruncatedLabel from "src/components/truncated-label/TruncatedLabel";
import { buildColumnBlocks } from "src/sections/projects/LLMTracing/evalTaskGrouping";
import TaskGroupHeader from "./TaskGroupHeader";

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
    if (c?.id) acc[c.id] = value;
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
const DraggableColumnRow = ({ id, name, checked, onChange, indent }) => {
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
        pl: indent ? "20px" : "4px",
        pr: "4px",
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
      <TruncatedLabel text={name} />
    </Box>
  );
};

DraggableColumnRow.propTypes = {
  id: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  checked: PropTypes.bool,
  onChange: PropTypes.func,
  indent: PropTypes.bool,
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
  useGrouping: _useGrouping = false,
  placement = "bottom",
}) => {
  const theme = useTheme();
  const [searchQuery, setSearchQuery] = useState("");
  const [collapsedTasks, setCollapsedTasks] = useState(() => new Set());
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 300);

  const toggleTaskCollapse = (taskKey) =>
    setCollapsedTasks((prev) => {
      const next = new Set(prev);
      if (next.has(taskKey)) next.delete(taskKey);
      else next.add(taskKey);
      return next;
    });

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

  const { blocks, dragBlocks, sortableIds } = useMemo(() => {
    const blockList = buildColumnBlocks(filteredColumns || []);
    const drag = blockList.map((b) =>
      b.type === "col"
        ? { type: "col", ids: b.col?.id ? [b.col.id] : [] }
        : {
            type: "task",
            headerId: `task:${b.group?.key}`,
            ids: (b.group?.evals || []).map((c) => c?.id).filter(Boolean),
          },
    );
    return {
      blocks: blockList,
      dragBlocks: drag,
      sortableIds: drag.flatMap((b) => [
        ...(b.headerId ? [b.headerId] : []),
        ...b.ids,
      ]),
    };
  }, [filteredColumns]);

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

  // Drags map displayed (grouped) order back onto the flat columns array;
  // cross-task eval drops are ignored — membership comes from the eval task id.
  function handleDragEnd(event) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const blockOf = (id) =>
      dragBlocks.findIndex((b) => b.headerId === id || b.ids.includes(id));
    const fromBlock = blockOf(active.id);
    const toBlock = blockOf(over.id);
    if (fromBlock === -1 || toBlock === -1) return;

    const isHeader = (id) => String(id).startsWith("task:");
    let newDisplayedIds;
    if (isHeader(active.id) || dragBlocks[fromBlock].type === "col") {
      if (fromBlock === toBlock) return;
      newDisplayedIds = arrayMove(dragBlocks, fromBlock, toBlock).flatMap(
        (b) => b.ids,
      );
    } else if (fromBlock === toBlock) {
      const ids = dragBlocks[fromBlock].ids;
      const moved = arrayMove(
        ids,
        ids.indexOf(active.id),
        ids.indexOf(over.id),
      );
      newDisplayedIds = dragBlocks.flatMap((b, i) =>
        i === fromBlock ? moved : b.ids,
      );
    } else {
      return;
    }

    const displayedSet = new Set(newDisplayedIds);
    const byId = new Map((columns || []).map((c) => [c?.id, c]));
    let next = 0;
    const reordered = (columns || []).map((c) =>
      displayedSet.has(c?.id) ? byId.get(newDisplayedIds[next++]) : c,
    );
    setColumns(reordered);
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
            items={sortableIds}
            strategy={verticalListSortingStrategy}
          >
            {blocks.map((block) => {
              if (block.type === "col") {
                const column = block.col;
                return column?.id ? (
                  <DraggableColumnRow
                    key={column.id}
                    id={column.id}
                    name={column.name || column.id}
                    checked={!!column.isVisible}
                    onChange={(e) => {
                      onColumnChange({ [column.id]: e.target.checked });
                    }}
                  />
                ) : null;
              }
              const task = block.group;
              const evals = task?.evals || [];
              const state = aggregateState(evals);
              const isCollapsed = collapsedTasks.has(task?.key);
              return (
                <React.Fragment key={task?.key}>
                  <TaskGroupHeader
                    dragId={`task:${task?.key}`}
                    label={task?.taskName}
                    checked={state === SELECTION_STATE.ALL}
                    indeterminate={state === SELECTION_STATE.SOME}
                    onToggle={(value) =>
                      onColumnChange(toggleMap(evals, value))
                    }
                    collapsed={isCollapsed}
                    onCollapseToggle={() => toggleTaskCollapse(task?.key)}
                  />
                  {!isCollapsed &&
                    evals.map((column) =>
                      column?.id ? (
                        <DraggableColumnRow
                          key={column.id}
                          id={column.id}
                          name={column.name || column.id}
                          checked={!!column.isVisible}
                          indent
                          onChange={(e) => {
                            onColumnChange({ [column.id]: e.target.checked });
                          }}
                        />
                      ) : null,
                    )}
                </React.Fragment>
              );
            })}
          </SortableContext>
        </DndContext>

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
