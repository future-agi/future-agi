import {
  Box,
  Divider,
  FormControlLabel,
  IconButton,
  Popover,
  Skeleton,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { LightCheckbox } from "src/sections/project-detail/StyledComponents";
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
import SvgColor from "src/components/svg-color";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from "src/sections/develop-detail/AccordianElements";
import { useDebounce } from "src/hooks/use-debounce";
import FormSearchField from "../FormSearchField/FormSearchField";

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
  };

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
            checkedIcon={<Iconify icon="mdi:checkbox-marked" />}
            icon={
              <Iconify
                icon="carbon:checkbox"
                sx={{
                  color: "divider",
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
              borderRadius: "8px",
              display: "inline-block",
              px: "4px",
              py: "2px",
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
                fontColor: "text.disabled",
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

const ColumnDropdown = ({
  open,
  onClose,
  anchorEl,
  columns,
  setColumns,
  onColumnVisibilityChange,
  nameKey = "name",
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const columnData = useMemo(() => {
    const arr = [...columns];
    arr.forEach((column) => {
      if (column.children) {
        for (let index = 0; index < column.children.length; index++) {
          const element = column.children[index];
          element.isVisible = column.isVisible;
        }
        column.isVisible = column.children.every((child) => child.isVisible);
      } else if (column.isVisible === undefined) {
        column.isVisible = true;
      }
      return column;
    });
    return arr;
  }, [columns]);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

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

  function handleChildDragEnd(event, group, columns) {
    const { active, over } = event;
    if (active.id !== over.id) {
      const oldIndex = columns.findIndex((item) => item.id === active.id);
      const newIndex = columns.findIndex((item) => item.id === over.id);
      const updatedCols = arrayMove(columns, oldIndex, newIndex);
      const newColumnData = columnData.map(([groupName, groupCols]) => {
        if (groupName === group) {
          return [groupName, updatedCols];
        }
        return [groupName, groupCols];
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
          minWidth: "250px",
        },
      }}
    >
      <Box
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 2,
          backgroundColor: "background.paper",
          padding: (theme) => theme.spacing(1),
        }}
      >
        <FormSearchField
          size="small"
          placeholder="Search"
          disableUnderline
          searchQuery={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          sx={{
            width: "100%",
          }}
        />
      </Box>
      <Divider />
      {!columns || columns.length === 0 ? (
        <Box sx={{ p: 2 }}>
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} height={24} sx={{ my: 1 }} />
          ))}
        </Box>
      ) : (
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
              {(() => {
                const filteredColumns = columnData.filter((column) => {
                  const childrenMatch =
                    (column?.children ?? []).filter((col) =>
                      col?.[nameKey]
                        ?.toLowerCase()
                        ?.includes(debouncedSearchQuery?.toLowerCase()),
                    )?.length > 0;

                  const parentMatch = column?.[nameKey]
                    ?.toLowerCase()
                    ?.includes(debouncedSearchQuery?.toLowerCase());

                  return childrenMatch || parentMatch;
                });

                if (filteredColumns?.length === 0) {
                  return (
                    <Box
                      sx={{ p: 2, color: "text.disabled", textAlign: "center" }}
                    >
                      No matching columns
                    </Box>
                  );
                }

                return filteredColumns?.map((column) => {
                  const isParentVisible = column?.[nameKey]
                    ?.toLowerCase()
                    ?.includes(debouncedSearchQuery?.toLowerCase());

                  return (
                    <Accordion
                      key={column.id}
                      defaultExpanded
                      sx={{ border: "none", gap: 0 }}
                    >
                      <AccordionSummary
                        sx={{
                          color: "text.primary",
                          minHeight: "0px",
                          maxHeight: "32px",
                          padding: 0,
                          margin: 0,
                        }}
                        expandIcon={
                          column?.children ? (
                            <Iconify
                              icon="gg:chevron-right"
                              sx={{ color: "text.disabled" }}
                              width={16}
                            />
                          ) : null
                        }
                      >
                        <DraggableDropdownItem
                          sx={{ marginLeft: 0 }}
                          id={column.id}
                          checked={column.isVisible}
                          onChange={() => {
                            onColumnVisibilityChange(column.id);
                          }}
                          label={column?.[nameKey]}
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
                                  col?.[nameKey]
                                    ?.toLowerCase()
                                    ?.includes(
                                      debouncedSearchQuery?.toLowerCase(),
                                    ) || isParentVisible,
                              )
                              .map((col) => (
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
                                  label={col?.[nameKey]}
                                  labelPlacement="end"
                                />
                              ))}
                          </SortableContext>
                        </DndContext>
                      </AccordionDetails>
                    </Accordion>
                  );
                });
              })()}
            </SortableContext>
          </Box>
        </DndContext>
      )}
    </Popover>
  );
};

ColumnDropdown.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchorEl: PropTypes.object,
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  nameKey: PropTypes.string,
  onColumnVisibilityChange: PropTypes.func,
};

export default ColumnDropdown;
