// Drag to move (W9-SCOPE §5 A6): a hover handle in the Name cell drags the
// dataset (or the selection it belongs to) onto a folder in the rail or
// the breadcrumb. While over a folder the overlay reads `Move 2 datasets
// to support`; the drop sends one PUT per dataset and the toast offers
// Undo (each dataset back in its own folder). Uses @dnd-kit/core.
import React, { useCallback, useState } from "react";
import PropTypes from "prop-types";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { Box, IconButton, Paper } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import { invalidateDatasetLists } from "src/api/develop/dataset-org";
import { datasetCaps } from "../realBackendCaps";
import { moveDatasetsToPath, folderErrorSentence } from "./realFolderApi";
import { showOrganizeToast } from "./useDatasetOrganize";
import {
  dropId,
  dropPlan,
  draggedDatasets,
  finishDrag,
  pathOfDropId,
} from "./folderDrag";

/** Wrap the list (rail + grid) so rows can be dropped on folders. */
/**
 * v2.1 g8 (org:drag-to-folder): the drop target is the droppable under the
 * pointer, never a neighbour whose box the dragged handle overlaps.
 */
export const FOLDER_COLLISION = pointerWithin;

export function FolderDndProvider({ selected, canEdit, children }) {
  const queryClient = useQueryClient();
  const [active, setActive] = useState(null); // {row, label}
  const sensors = useSensors(
    // A click on the handle stays a click; a drag starts after a move.
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
  );
  const onDragEnd = useCallback(
    async ({ active: a, over }) => {
      setActive(null);
      if (datasetCaps().real) {
        const path = pathOfDropId(over?.id);
        const plan =
          path === null
            ? null
            : dropPlan(draggedDatasets(a?.data?.current?.row, selected), path);
        if (!plan) return;
        try {
          await moveDatasetsToPath(plan.moving, path);
          await queryClient.invalidateQueries({ queryKey: ["datasets"] });
          showOrganizeToast({
            message: `Moved ${plan.moving.length} dataset${plan.moving.length === 1 ? "" : "s"}`,
            variant: "success",
          });
        } catch (error) {
          showOrganizeToast({
            message: folderErrorSentence(error),
            variant: "error",
          });
        }
        return;
      }
      const toast = await finishDrag({
        row: a?.data?.current?.row,
        overId: over?.id,
        selected,
      });
      if (!toast) return;
      invalidateDatasetLists(queryClient);
      showOrganizeToast(
        toast.undo
          ? {
              ...toast,
              undo: async () => {
                const res = await toast.undo();
                invalidateDatasetLists(queryClient);
                return res;
              },
            }
          : toast,
      );
    },
    [selected, queryClient],
  );
  if (!canEdit) return children;
  return (
    <DndContext
      sensors={sensors}
      // v2.1 g8 (org:drag-to-folder): the drop target is the folder under
      // the pointer (rect intersection picked a neighbour of the dragged
      // handle's box).
      collisionDetection={FOLDER_COLLISION}
      onDragStart={({ active: a }) =>
        setActive({ row: a?.data?.current?.row, label: null })
      }
      onDragOver={({ active: a, over }) => {
        const path = pathOfDropId(over?.id);
        const plan =
          path === null
            ? null
            : dropPlan(draggedDatasets(a?.data?.current?.row, selected), path);
        setActive((cur) =>
          cur ? { ...cur, label: plan?.label ?? null } : cur,
        );
      }}
      onDragCancel={() => setActive(null)}
      onDragEnd={onDragEnd}
    >
      {children}
      <DragOverlay dropAnimation={null}>
        {active ? (
          <Paper
            elevation={6}
            sx={{ px: 1.25, py: 0.75, typography: "s2", pointerEvents: "none" }}
            data-testid="folder-drag-overlay"
          >
            {active.label ??
              (draggedDatasets(active.row, selected).length > 1
                ? `${draggedDatasets(active.row, selected).length} datasets`
                : active.row?.name)}
          </Paper>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
FolderDndProvider.propTypes = {
  selected: PropTypes.array,
  canEdit: PropTypes.bool,
  children: PropTypes.node,
};

/** The hover handle in the Name cell. */
export function DatasetDragHandle({ row }) {
  const { attributes, listeners, setNodeRef } = useDraggable({
    id: `dataset-drag:${row.id}`,
    data: { row },
  });
  return (
    <IconButton
      ref={setNodeRef}
      size="small"
      aria-label={`Drag ${row.name ?? "dataset"} to a folder`}
      onClick={(e) => e.stopPropagation()}
      sx={{ p: 0.25, color: "text.disabled", cursor: "grab" }}
      {...attributes}
      {...listeners}
    >
      <Iconify icon="mdi:drag-vertical" width={16} />
    </IconButton>
  );
}
DatasetDragHandle.propTypes = {
  row: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string })
    .isRequired,
};

/** A folder drop target (rail row, breadcrumb segment). */
export function FolderDropZone({ path, children }) {
  const { setNodeRef, isOver } = useDroppable({ id: dropId(path) });
  return (
    <Box
      ref={setNodeRef}
      data-drop-path={path}
      sx={{
        borderRadius: 0.5,
        outline: isOver ? "2px solid" : "none",
        outlineColor: "primary.main",
      }}
    >
      {children}
    </Box>
  );
}
FolderDropZone.propTypes = {
  path: PropTypes.string.isRequired,
  children: PropTypes.node,
};
