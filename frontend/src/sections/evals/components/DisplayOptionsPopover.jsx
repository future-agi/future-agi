import {
  Box,
  Button,
  Checkbox,
  Divider,
  FormControlLabel,
  IconButton,
  Popover,
  Typography,
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
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useDebounce } from "src/hooks/use-debounce";

export const ALL_COLUMNS = [
  { field: "name", label: "Evaluation Name", locked: true },
  { field: "templateType", label: "Type" },
  { field: "evalType", label: "Eval Type" },
  { field: "outputType", label: "Output Type" },
  { field: "tags", label: "Tags" },
  { field: "thirtyDayChart", label: "30 day chart" },
  { field: "thirtyDayErrorRate", label: "30 day error rate" },
  { field: "createdByName", label: "Created By" },
  { field: "lastUpdated", label: "Last updated" },
  { field: "currentVersion", label: "Versions" },
];

export const DEFAULT_COLUMN_ORDER = ALL_COLUMNS.filter((c) => !c.locked).map(
  (c) => c.field,
);

const COLUMN_LOOKUP = Object.fromEntries(ALL_COLUMNS.map((c) => [c.field, c]));

const SortableColumnRow = ({ col, isVisible, onToggle }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: col.field });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : 1,
    opacity: isDragging ? 0.8 : 1,
  };

  return (
    <Box
      ref={setNodeRef}
      style={style}
      sx={{
        display: "flex",
        alignItems: "center",
        mx: 0,
        px: 1,
        py: 0.25,
        borderRadius: "4px",
        "&:hover": { backgroundColor: "action.hover" },
      }}
    >
      <IconButton
        {...attributes}
        {...listeners}
        size="small"
        sx={{
          mr: 0.25,
          color: "text.disabled",
          cursor: "grab",
          "&:active": { cursor: "grabbing" },
        }}
      >
        <Iconify icon="solar:grip-vertical-linear" width={16} />
      </IconButton>
      <FormControlLabel
        sx={{ mx: 0, flex: 1 }}
        control={
          <Checkbox
            size="small"
            checked={isVisible}
            onChange={() => onToggle(col.field)}
            sx={{ p: 0.5 }}
          />
        }
        label={
          <Typography variant="body2" sx={{ fontSize: "13px", ml: 0.5 }}>
            {col.label}
          </Typography>
        }
      />
    </Box>
  );
};

SortableColumnRow.propTypes = {
  col: PropTypes.object.isRequired,
  isVisible: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
};

const DisplayOptionsPopover = ({
  anchorEl,
  open,
  onClose,
  hiddenColumns,
  onToggleColumn,
  onReset,
  columnOrder,
  onReorderColumns,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearch = useDebounce(searchQuery.trim(), 300);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const lockedColumn = ALL_COLUMNS.find((c) => c.locked);

  const orderedColumns = useMemo(() => {
    const source =
      columnOrder && columnOrder.length > 0
        ? columnOrder
        : DEFAULT_COLUMN_ORDER;
    return source.map((id) => COLUMN_LOOKUP[id]).filter(Boolean);
  }, [columnOrder]);

  const matchesSearch = (col) =>
    col.label.toLowerCase().includes(debouncedSearch.toLowerCase());

  const visibleCount = ALL_COLUMNS.filter(
    (c) => !hiddenColumns.includes(c.field),
  ).length;

  const handleDragEnd = (event) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    if (!onReorderColumns) return;
    const current =
      columnOrder && columnOrder.length > 0
        ? columnOrder
        : DEFAULT_COLUMN_ORDER;
    const oldIndex = current.indexOf(active.id);
    const newIndex = current.indexOf(over.id);
    if (oldIndex === -1 || newIndex === -1) return;
    onReorderColumns(arrayMove(current, oldIndex, newIndex));
  };

  const isDefaultState =
    hiddenColumns.length === 0 &&
    JSON.stringify(columnOrder || DEFAULT_COLUMN_ORDER) ===
      JSON.stringify(DEFAULT_COLUMN_ORDER);

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: -8, horizontal: "right" }}
      slotProps={{
        paper: {
          sx: {
            p: 0,
            minWidth: 320,
            maxHeight: 400,
            borderRadius: "8px",
          },
        },
      }}
    >
      <Box sx={{ display: "flex", flexDirection: "column" }}>
        {/* Sticky search header */}
        <Box
          sx={{
            position: "sticky",
            top: 0,
            zIndex: 10,
            backgroundColor: "background.paper",
          }}
        >
          <Box sx={{ p: 1.5, pb: 1 }}>
            <FormSearchField
              size="small"
              fullWidth
              placeholder="Search columns"
              searchQuery={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </Box>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              px: 2,
              pb: 0.5,
            }}
          >
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "12px" }}
            >
              {visibleCount} of {ALL_COLUMNS.length} visible
            </Typography>
            {onReset && (
              <Button
                size="small"
                onClick={onReset}
                disabled={isDefaultState}
                sx={{
                  minWidth: 0,
                }}
              >
                Reset
              </Button>
            )}
          </Box>
          <Divider />
        </Box>

        {/* Column list */}
        <Box sx={{ p: 1, display: "flex", flexDirection: "column" }}>
          {/* Locked row — pinned, not draggable */}
          {lockedColumn && matchesSearch(lockedColumn) && (
            <FormControlLabel
              sx={{
                mx: 0,
                px: 1,
                py: 0.25,
                ml: "22px", // align with sortable rows (past drag handle)
                borderRadius: "4px",
                "&:hover": { backgroundColor: "action.hover" },
                opacity: 0.6,
              }}
              control={
                <Checkbox
                  size="small"
                  checked={!hiddenColumns.includes(lockedColumn.field)}
                  disabled
                  sx={{ p: 0.5 }}
                />
              }
              label={
                <Typography variant="body2" sx={{ fontSize: "13px", ml: 0.5 }}>
                  {lockedColumn.label}
                  <Iconify
                    icon="solar:lock-linear"
                    width={12}
                    sx={{
                      ml: 0.5,
                      verticalAlign: "text-bottom",
                      color: "text.disabled",
                    }}
                  />
                </Typography>
              }
            />
          )}

          {/* Sortable rows */}
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={orderedColumns.map((c) => c.field)}
              strategy={verticalListSortingStrategy}
            >
              {orderedColumns.filter(matchesSearch).map((col) => (
                <SortableColumnRow
                  key={col.field}
                  col={col}
                  isVisible={!hiddenColumns.includes(col.field)}
                  onToggle={onToggleColumn}
                />
              ))}
            </SortableContext>
          </DndContext>
        </Box>
      </Box>
    </Popover>
  );
};

DisplayOptionsPopover.propTypes = {
  anchorEl: PropTypes.any,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  hiddenColumns: PropTypes.arrayOf(PropTypes.string).isRequired,
  onToggleColumn: PropTypes.func.isRequired,
  onReset: PropTypes.func,
  columnOrder: PropTypes.arrayOf(PropTypes.string),
  onReorderColumns: PropTypes.func,
};

export default DisplayOptionsPopover;
