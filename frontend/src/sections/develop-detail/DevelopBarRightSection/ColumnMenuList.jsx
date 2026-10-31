import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Checkbox,
  Collapse,
  IconButton,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { DndContext, closestCenter } from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { arrayMove } from "@dnd-kit/sortable";
import Iconify from "src/components/iconify";
import React from "react";

// Draggable wrapper
const DraggableColumnMenuItem = ({
  childProps,
  isDragging,
  moveColumn,
  parentIndex,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
  } = useSortable({
    id: childProps.id,
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={style}>
      <ColumnMenuItem
        {...childProps}
        dragHandleProps={{ setActivatorNodeRef, listeners, attributes }}
        moveColumn={moveColumn}
        parentIndex={parentIndex}
      />
    </div>
  );
};

DraggableColumnMenuItem.propTypes = {
  childProps: PropTypes.shape({
    id: PropTypes.string,
    headerName: PropTypes.string.isRequired,
    handleColumnSelect: PropTypes.func.isRequired,
    selectedColumns: PropTypes.array.isRequired,
    children: PropTypes.array,
    sx: PropTypes.object,
  }).isRequired,
  isDragging: PropTypes.bool,
  moveColumn: PropTypes.func,
  parentIndex: PropTypes.number,
  id: PropTypes.string,
};

// ColumnMenuItem
const ColumnMenuItem = ({
  children = [],
  handleColumnSelect,
  headerName,
  selectedColumns,
  sx,
  dragHandleProps,
  moveColumn,
  parentIndex,
  id,
}) => {
  const isChecked = selectedColumns.includes(id);
  const [collapseChildren, setCollapseChildren] = useState(true);

  const [childOrder, setChildOrder] = useState(
    Array.isArray(children) ? children.map((c) => c.id) : [],
  );

  const [isDragging, setIsDragging] = useState(false);

  const onChildDragEnd = (event) => {
    const { active, over } = event;
    const idToMove = children?.find((child) => child?.id === active?.id)?.field;
    if (!over || active.id === over.id) return;
    setChildOrder((items) => {
      const oldIndex = items.indexOf(active.id);
      const newIndex = items.indexOf(over.id);
      moveColumn([idToMove], parentIndex + newIndex);
      return arrayMove(items, oldIndex, newIndex);
    });
  };

  const onDragStart = () => {
    setCollapseChildren(true); // Collapse the parent immediately when dragging starts
    setIsDragging(true); // Set dragging state to true
  };

  const onDragEnd = (event) => {
    setIsDragging(false); // Reset dragging state to false

    // If the dragged item is a child, handle its reordering
    if (event.active.id !== event.over.id) {
      onChildDragEnd(event);
    }

    // You can reset the collapse state here or leave it as is if you want to maintain it
    // setCollapseChildren(false); // Optional if you want to expand it again after the drag ends
  };

  return (
    <>
      <MenuItem
        key={id}
        value={id}
        onClick={() => handleColumnSelect(id)}
        sx={{
          px: "4px",
          py: "0px",
          display: "flex",
          flexDirection: "row",
          columnGap: "6px",
          marginLeft: children.length < 1 ? "33px" : "0",
          ...sx,
        }}
        dense
      >
        {children.length > 0 && (
          <IconButton
            onClick={(e) => {
              e.stopPropagation();
              setCollapseChildren((prev) => !prev);
            }}
            sx={{ pl: 0 }}
            size="medium"
          >
            <Iconify
              sx={{
                transition: "transform 0.2s ease",
                transform: !collapseChildren
                  ? "rotate(270deg)"
                  : "rotate(0deg)",
              }}
              icon="solar:alt-arrow-down-linear"
            />
          </IconButton>
        )}

        <Checkbox
          checked={isChecked}
          size="medium"
          sx={{
            borderRadius: "4px",
            mt: "0px",
            ml: "24px",
            "& .MuiSvgIcon-root": {
              fontSize: "20px",
            },
          }}
          inputProps={{ "aria-label": "Checkbox Demo" }}
        />

        <IconButton
          {...(dragHandleProps ? dragHandleProps.listeners : {})}
          {...(dragHandleProps ? dragHandleProps.attributes : {})}
          ref={dragHandleProps ? dragHandleProps.setActivatorNodeRef : null}
          onMouseDown={(e) => e.stopPropagation()}
          onTouchStart={(e) => e.stopPropagation()}
          sx={{ cursor: "grab" }}
        >
          <Box
            sx={{
              height: "16px",
              width: "16px",
            }}
            component="img"
            src="/assets/icons/ic_dragger_rect.svg"
          />
        </IconButton>

        <Typography
          fontSize={13}
          fontWeight={400}
          color="text.primary"
          marginLeft="4px"
        >
          {headerName}
        </Typography>
      </MenuItem>

      {children.length > 0 && (
        <Collapse in={collapseChildren}>
          <DndContext
            collisionDetection={closestCenter}
            onDragEnd={onDragEnd} // One handler for both child drag and collapse logic
            onDragStart={onDragStart} // Collapse parent when drag starts
          >
            <SortableContext
              items={childOrder}
              strategy={verticalListSortingStrategy}
            >
              <Stack direction="column" sx={{ pl: "8px" }}>
                {childOrder.map((childName) => {
                  const child = children.find((c) => c.id === childName);

                  if (!child) return null;
                  return (
                    <DraggableColumnMenuItem
                      childProps={{
                        ...child,
                        handleColumnSelect: () =>
                          handleColumnSelect(`${child?.id}`, id),
                        selectedColumns,
                        sx: { ml: "33px" },
                      }}
                      key={`${child?.id}`}
                      id={`${child?.id}`}
                      isDragging={isDragging}
                      moveColumn={moveColumn}
                      parentIndex={parentIndex}
                    />
                  );
                })}
              </Stack>
            </SortableContext>
          </DndContext>
        </Collapse>
      )}
    </>
  );
};

ColumnMenuItem.propTypes = {
  headerName: PropTypes.string.isRequired,
  handleColumnSelect: PropTypes.func.isRequired,
  selectedColumns: PropTypes.arrayOf(PropTypes.string).isRequired,
  children: PropTypes.arrayOf(
    PropTypes.shape({
      headerName: PropTypes.string.isRequired,
      children: PropTypes.array,
    }),
  ),
  sx: PropTypes.object,
  dragHandleProps: PropTypes.object,
  moveColumn: PropTypes.func,
  parentIndex: PropTypes.number,
  id: PropTypes.string,
};

// Main component that renders the parent columns
const ColumnMenuList = ({
  data,
  selectedColumns,
  handleColumnSelect,
  moveColumn,
}) => {
  const [parentOrder, setParentOrder] = useState(data.map((item) => item.id));

  const onParentDragEnd = (event) => {
    const { active, over } = event;
    const parent = data.find((d) => d.id === active?.id);
    const overParent = data.find((d) => d?.id === over?.id);
    if (!over || active.id === over.id) return;
    const idsToMove =
      parent?.children && parent?.children?.length > 0
        ? parent?.children?.map((child) => child?.field)
        : [parent?.field];
    setParentOrder((items) => {
      const oldIndex = items.indexOf(active.id);
      const newIndex = items.indexOf(over.id);
      const additionalJump =
        newIndex > 0 && overParent?.children?.length > 0
          ? overParent?.children?.length - 1
          : 0;
      moveColumn(idsToMove, newIndex + additionalJump);
      return arrayMove(items, oldIndex, newIndex);
    });
  };

  return (
    <DndContext collisionDetection={closestCenter} onDragEnd={onParentDragEnd}>
      <SortableContext
        items={parentOrder}
        strategy={verticalListSortingStrategy}
      >
        <Stack direction="column">
          {parentOrder.map((parentName, index) => {
            const parent = data.find((d) => d.id === parentName);
            if (!parent) return null;
            return (
              <DraggableColumnMenuItem
                key={parent.id}
                childProps={{
                  ...parent,
                  handleColumnSelect,
                  selectedColumns,
                }}
                isDragging={false}
                moveColumn={moveColumn}
                parentIndex={parent?.children?.length + index - 1}
              />
            );
          })}
        </Stack>
      </SortableContext>
    </DndContext>
  );
};

ColumnMenuList.propTypes = {
  data: PropTypes.arrayOf(
    PropTypes.shape({
      headerName: PropTypes.string.isRequired,
      children: PropTypes.array,
    }),
  ).isRequired,
  selectedColumns: PropTypes.arrayOf(PropTypes.string).isRequired,
  handleColumnSelect: PropTypes.func.isRequired,
  moveColumn: PropTypes.func,
};

export default ColumnMenuList;
