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
import {
  Box,
  Divider,
  FormControlLabel,
  IconButton,
  Popover,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { LightCheckbox } from "src/sections/project-detail/StyledComponents";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "../AccordianElements";
import { useDebounce } from "src/hooks/use-debounce";
import FormSearchField from "src/components/FormSearchField/FormSearchField";

const DraggableDropdownItem = (props) => {
  const { id, checked, onChange, parent, ...rest } = props;
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : 1,
    width: "100%",
  };
  const theme = useTheme();

  return (
    <FormControlLabel
      ref={setNodeRef}
      style={style}
      onClick={(e) => e.stopPropagation()}
      control={
        <>
          <LightCheckbox
            checked={checked}
            onChange={onChange}
            sx={{
              padding: "0px",
              "&.Mui-checked": { color: "primary.light" },
            }}
            checkedIcon={<Iconify icon="solar:check-square-bold" />}
            icon={
              <Iconify
                icon="solar:square-linear"
                sx={{
                  color: theme.palette.divider,
                }}
              />
            }
            inputProps={{
              "aria-label": "controlled",
            }}
          />
          <IconButton
            {...attributes}
            {...listeners}
            sx={{
              display: "inline-block",
              ":hover": {
                cursor: "grab",
              },
              ":active": {
                cursor: "grabbing",
              },
            }}
          >
            <SvgColor
              sx={{
                width: "16px",
                height: "16px",
                fontColor: "text.secondary",
              }}
              src="/assets/icons/custom/grip.svg"
            />
          </IconButton>
        </>
      }
      {...rest}
    />
  );
};

DraggableDropdownItem.propTypes = {
  id: PropTypes.string,
  checked: PropTypes.bool,
  onChange: PropTypes.func,
  parent: PropTypes.bool,
  sx: PropTypes.object,
  label: PropTypes.string,
  labelPlacement: PropTypes.string,
};

function ColumnMenu({
  open,
  anchorEl,
  onClose,
  columns,
  onColumnVisibilityChange,
  selectedColumnIds,
  setColumns,
}) {
  const theme = useTheme();
  const [searchQuery, setSearchQuery] = useState("");
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const columnData = useMemo(() => {
    const arr = [...columns];
    arr.forEach((column) => {
      if (column.children) {
        for (let index = 0; index < column.children.length; index++) {
          const element = column.children[index];
          element.isVisible =
            selectedColumnIds.findIndex(
              (columnId) => columnId === element.id,
            ) !== -1;
        }
        column.isVisible = column.children.every((child) => child.isVisible);
      } else {
        column.isVisible =
          selectedColumnIds.findIndex((columnId) => columnId === column.id) !==
          -1;
      }
      return column;
    });
    return arr;
  }, [columns, selectedColumnIds]);

  function handleDragEnd(event) {
    const { active, over } = event;
    if (active.id !== over.id) {
      const oldIndex = columnData.findIndex((item) => item.id == active.id);
      const newIndex = columnData.findIndex((item) => item.id == over.id);
      const newColumnData = arrayMove(columnData, oldIndex, newIndex);
      setColumns(newColumnData);
      // onColumnOrderChange(newColumnData);
    }
  }

  function handleChildDragEnd(event, parent, children) {
    const { active, over } = event;
    if (active.id !== over.id) {
      const oldIndex = children.findIndex((item) => item.id === active.id);
      const newIndex = children.findIndex((item) => item.id === over.id);
      const updatedCols = arrayMove(children, oldIndex, newIndex);
      const newColumnData = columnData.map((group) => {
        if (parent === group.id) {
          group.children = updatedCols;
        }
        return group;
      });
      setColumns(newColumnData);
    }
  }

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorEl={anchorEl}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "right",
      }}
      transformOrigin={{
        vertical: -14,
        horizontal: "right",
      }}
      PaperProps={{
        sx: {
          maxHeight: "400px",
          overflowY: "auto",
          padding: 0,
          minWidth: "320px",
        },
      }}
    >
      <Box
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 2,
          background: "background.paper",
        }}
      >
        <Box
          sx={{
            margin: "12px",
          }}
        >
          <FormSearchField
            fullWidth
            placeholder="Search"
            disableUnderline
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            sx={{ flex: 1 }}
          />
        </Box>
        <Divider />
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <Box sx={{ p: 1, display: "flex", flexDirection: "column" }}>
            <SortableContext
              items={columnData}
              strategy={verticalListSortingStrategy}
            >
              {columnData.map((column) => {
                const areChildrenVisible =
                  (column?.children ?? []).filter((col) =>
                    col?.headerName
                      ?.toLowerCase()
                      .includes(debouncedSearchQuery?.toLowerCase()),
                  )?.length > 0;
                const isParentVisible = column?.headerName
                  ?.toLowerCase()
                  ?.includes(debouncedSearchQuery?.toLowerCase());

                if (areChildrenVisible || isParentVisible)
                  return (
                    <Accordion
                      key={column.id}
                      defaultExpanded
                      sx={{
                        border: "none",
                        gap: 0,
                      }}
                    >
                      <AccordionSummary
                        sx={{
                          color: theme.palette.text.primary,
                          minHeight: "0px",
                          maxHeight: "32px",
                          padding: 0,
                          margin: 0,
                        }}
                        expandIcon={
                          <Iconify
                            icon="solar:alt-arrow-right-linear"
                            sx={{ color: "text.secondary" }}
                            width={16}
                          />
                        }
                      >
                        <DraggableDropdownItem
                          sx={{ marginLeft: 0 }}
                          id={column.id}
                          checked={column.isVisible}
                          onChange={() => {
                            onColumnVisibilityChange(column.id);
                          }}
                          label={column.headerName}
                          labelPlacement="end"
                        />
                      </AccordionSummary>
                      <AccordionDetails
                        sx={{
                          display: "flex",
                          flexDirection: "column",
                          paddingLeft: 5,
                          py: 0,
                        }}
                      >
                        <DndContext
                          sensors={sensors}
                          collisionDetection={closestCenter}
                          onDragEnd={(e) =>
                            handleChildDragEnd(
                              e,
                              column.id,
                              column?.children ?? [],
                            )
                          }
                        >
                          <SortableContext
                            items={column?.children ?? []}
                            strategy={verticalListSortingStrategy}
                          >
                            {(column?.children ?? [])
                              .filter(
                                (col) =>
                                  col?.headerName
                                    ?.toLowerCase()
                                    ?.includes(
                                      debouncedSearchQuery?.toLowerCase(),
                                    ) || isParentVisible,
                              )
                              .map((col) => {
                                return (
                                  <DraggableDropdownItem
                                    key={col?.id}
                                    id={col?.id}
                                    checked={col?.isVisible}
                                    sx={{ marginLeft: 0 }}
                                    onChange={() => {
                                      onColumnVisibilityChange(
                                        col?.id,
                                        column.id,
                                      );
                                    }}
                                    label={col?.headerName}
                                    labelPlacement="end"
                                  />
                                );
                              })}
                          </SortableContext>
                        </DndContext>
                      </AccordionDetails>
                    </Accordion>
                  );
              })}
            </SortableContext>
          </Box>
        </DndContext>
      </Box>
    </Popover>
  );
}

ColumnMenu.propTypes = {
  open: PropTypes.bool,
  anchorEl: PropTypes.object,
  onClose: PropTypes.func,
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  onColumnVisibilityChange: PropTypes.func,
  selectedColumnIds: PropTypes.array,
};

export default ColumnMenu;
