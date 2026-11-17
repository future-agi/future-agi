import { LoadingButton } from "@mui/lab";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Paper,
  Typography,
} from "@mui/material";
import React, { useEffect, useState } from "react";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import {
  closestCenter,
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  rectSortingStrategy,
  SortableContext,
  useSortable,
} from "@dnd-kit/sortable";
import SecondaryCheckbox from "src/components/secondary-checkbox/SecondaryCheckbox";

const CustomizeColumnsModal = ({
  open,
  onClose,
  columns,
  onSaveClick,
  saveLoading,
}) => {
  const sensors = useSensors(useSensor(PointerSensor));

  const [activeColumn, setActiveColumn] = useState(null);

  const [innerColumnState, setInnerColumnState] = useState(columns);

  useEffect(() => {
    if (!innerColumnState) {
      setInnerColumnState(columns);
    }
  }, [columns]);

  const handleDragEnd = (event) => {
    setActiveColumn(null);
    const { active, over } = event;

    if (active.id !== over.id) {
      setInnerColumnState((items) => {
        const oldIndex = items.indexOf(active.id);
        const newIndex = items.indexOf(over.id);

        return arrayMove(items, oldIndex, newIndex);
      });
    }
  };

  const handleDragOver = (event) => {
    const { active, over } = event;

    if (active.id !== over.id) {
      setInnerColumnState((items) => {
        const oldIndex = items.findIndex((c) => c.value === active.id);
        const newIndex = items.findIndex((c) => c.value === over.id);

        return arrayMove(items, oldIndex, newIndex);
      });
    }
  };

  const handleDragStart = (event) => {
    const column = innerColumnState.find((c) => c.value === event.active.id);
    setActiveColumn(column);
  };

  return (
    <Dialog
      open={open}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 560 } }}
      onClose={onClose}
    >
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box sx={{ gap: 2, display: "flex", alignItems: "center" }}>
          <Iconify icon="solar:widget-4-linear" />
          Customize columns
        </Box>
        <IconButton onClick={() => onClose()}>
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
        >
          <SortableContext
            items={innerColumnState}
            strategy={rectSortingStrategy}
          >
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: "12px",
                paddingY: 1,
                maxHeight: "400px",
                overflowY: "auto",
              }}
            >
              {innerColumnState?.map((column) => (
                <ColumnItem
                  key={column.value}
                  column={column}
                  id={column.value}
                  enabled={column.enabled}
                  setEnabled={(newEnabled) => {
                    setInnerColumnState(() =>
                      innerColumnState?.map((col) => {
                        if (col.value === column.value) {
                          return { ...column, enabled: newEnabled };
                        }
                        return col;
                      }),
                    );
                  }}
                />
              ))}
              <DragOverlay>
                {activeColumn ? (
                  <ColumnItem
                    column={activeColumn}
                    id={activeColumn?.value}
                    enabled={activeColumn?.enabled}
                    setEnabled={() => {}}
                  />
                ) : null}
              </DragOverlay>
            </Box>
          </SortableContext>
        </DndContext>
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={() => onClose()} variant="outlined">
          Cancel
        </Button>
        <LoadingButton
          onClick={() => onSaveClick(innerColumnState)}
          loading={saveLoading}
          variant="contained"
          color="primary"
          size="small"
        >
          Save
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
};

CustomizeColumnsModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  columns: PropTypes.array,
  onSaveClick: PropTypes.func,
  saveLoading: PropTypes.bool,
};

const ColumnItem = ({ column, id, enabled, setEnabled }) => {
  const { attributes, listeners, setNodeRef, transition, isDragging } =
    useSortable({ id });

  const style = {
    transition,
    zIndex: isDragging ? "100" : "auto",
    opacity: isDragging ? 0.3 : 1,
  };

  return (
    <Paper
      sx={{ paddingX: "12px", paddingY: "18px" }}
      elevation={1}
      ref={setNodeRef}
      style={style}
    >
      <Box sx={{ display: "flex", alignItems: "center" }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 3,
            flex: 1,
          }}
        >
          <SecondaryCheckbox
            checked={enabled}
            onChange={(e, checked) => setEnabled(checked)}
            sx={{ padding: 0 }}
            color="secondary"
          />
          <Typography fontSize="13px" fontWeight={400} color="text.primary">
            {column?.label}
          </Typography>
        </Box>
        <Box {...listeners} {...attributes}>
          <Iconify
            icon="solar:hamburger-menu-linear"
            sx={{
              color: "text.disabled",
              cursor: isDragging ? "grabbing" : "grab",
            }}
          />
        </Box>
      </Box>
    </Paper>
  );
};

ColumnItem.propTypes = {
  id: PropTypes.string,
  column: PropTypes.object,
  enabled: PropTypes.bool,
  setEnabled: PropTypes.func,
};

export default CustomizeColumnsModal;
