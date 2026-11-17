import {
  Box,
  Checkbox,
  Divider,
  FormControlLabel,
  IconButton,
  Popover,
  Skeleton,
  Typography,
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

/**
 * Grouped list (optional `groups` prop): a header per group with a tri-state
 * checkbox, then its columns. Used by the dataset Data grid to group columns
 * by role (Input, Expected, Metadata, Generated outputs, Evals, …, System).
 */
const GroupedColumnList = ({
  groups,
  columns,
  nameKey,
  query,
  onColumnVisibilityChange,
}) => {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const q = (query ?? "").toLowerCase();
  const rendered = groups
    .map((g) => {
      const items = g.ids
        .map((id) => byId.get(id))
        .filter(Boolean)
        .filter(
          (c) =>
            !q ||
            String(c?.[nameKey] ?? "")
              .toLowerCase()
              .includes(q),
        );
      return { ...g, items };
    })
    .filter((g) => g.items.length);
  if (!rendered.length) {
    return (
      <Box sx={{ p: 2, color: "text.disabled", textAlign: "center" }}>
        No matching columns
      </Box>
    );
  }
  return (
    <Box sx={{ py: 0.5 }}>
      {rendered.map((g) => {
        const visible = g.items.filter((c) => c.isVisible !== false).length;
        const all = visible === g.items.length;
        const some = visible > 0 && !all;
        return (
          <Box key={g.key ?? g.label} sx={{ pb: 0.5 }}>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                px: 1.5,
                py: 0.5,
              }}
            >
              <Checkbox
                size="small"
                checked={all}
                indeterminate={some}
                onChange={() =>
                  g.items
                    .filter((c) => (c.isVisible !== false) === all)
                    .forEach((c) => onColumnVisibilityChange(c.id))
                }
                sx={{ p: 0 }}
                inputProps={{ "aria-label": `Show all ${g.label}` }}
              />
              <Typography
                typography="s3"
                fontWeight="fontWeightSemiBold"
                color="text.secondary"
                sx={{ textTransform: "uppercase", letterSpacing: "0.04em" }}
              >
                {g.label}
              </Typography>
              <Typography typography="s3" color="text.disabled">
                {visible}/{g.items.length}
              </Typography>
            </Box>
            {g.items.map((c) => (
              <FormControlLabel
                key={c.id}
                sx={{ display: "flex", mx: 0, pl: 4.5, pr: 1.5, py: 0.25 }}
                control={
                  <Checkbox
                    size="small"
                    checked={c.isVisible !== false}
                    onChange={() => onColumnVisibilityChange(c.id)}
                    sx={{ p: 0, mr: 1 }}
                  />
                }
                label={
                  <Typography typography="s2" noWrap sx={{ maxWidth: 220 }}>
                    {c?.[nameKey]}
                  </Typography>
                }
              />
            ))}
          </Box>
        );
      })}
    </Box>
  );
};

GroupedColumnList.propTypes = {
  groups: PropTypes.array,
  columns: PropTypes.array,
  nameKey: PropTypes.string,
  query: PropTypes.string,
  onColumnVisibilityChange: PropTypes.func,
};

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
            sx={{ p: 0 }}
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
  groups,
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
          minWidth: "320px",
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
      ) : groups?.length ? (
        <GroupedColumnList
          groups={groups}
          columns={columnData}
          nameKey={nameKey}
          query={debouncedSearchQuery}
          onColumnVisibilityChange={onColumnVisibilityChange}
        />
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
                              icon="solar:alt-arrow-right-linear"
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
  // Optional: [{key, label, ids}] renders a grouped list (Observe passes none).
  groups: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string,
      label: PropTypes.string.isRequired,
      ids: PropTypes.arrayOf(PropTypes.string).isRequired,
    }),
  ),
};

export default ColumnDropdown;
