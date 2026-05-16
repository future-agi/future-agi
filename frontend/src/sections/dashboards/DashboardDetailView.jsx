/* eslint-disable react/prop-types */
import React, {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Box,
  Breadcrumbs,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  ClickAwayListener,
  Divider,
  IconButton,
  InputBase,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";
import { paths } from "src/routes/paths";
import {
  useDashboardDetail,
  useUpdateDashboard,
  useUpdateWidget,
  useDeleteWidget,
  useDeleteDashboard,
  useReorderWidgets,
  useDuplicateWidget,
  useCreateWidget,
} from "src/hooks/useDashboards";
import Iconify from "src/components/iconify";
import ShareDialog from "src/components/share-dialog/ShareDialog";
import WidgetChart from "./WidgetChart";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  useDraggable,
  useDroppable,
} from "@dnd-kit/core";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const DATE_PRESETS = [
  { label: "Custom", value: "custom" },
  { label: "Today", value: "today" },
  { label: "Yesterday", value: "yesterday" },
  { label: "7D", value: "7D" },
  { label: "30D", value: "30D" },
  { label: "3M", value: "3M" },
  { label: "6M", value: "6M" },
  { label: "12M", value: "12M" },
];

const WIDTH_OPTIONS = [
  { label: "1/4 width", value: 3, icon: "mdi:view-column-outline" },
  { label: "1/3 width", value: 4, icon: "mdi:view-column-outline" },
  { label: "1/2 width", value: 6, icon: "mdi:view-split-vertical" },
  { label: "Full width", value: 12, icon: "mdi:view-sequential-outline" },
];

const MIN_WIDGET_HEIGHT = 120;
const DEFAULT_WIDGET_HEIGHT = 320;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getDateRange(preset) {
  const now = new Date();
  const start = new Date();
  switch (preset) {
    case "today":
      start.setHours(0, 0, 0, 0);
      break;
    case "yesterday":
      start.setDate(start.getDate() - 1);
      start.setHours(0, 0, 0, 0);
      now.setDate(now.getDate() - 1);
      now.setHours(23, 59, 59, 999);
      break;
    case "7D":
      start.setDate(start.getDate() - 7);
      break;
    case "30D":
      start.setDate(start.getDate() - 30);
      break;
    case "3M":
      start.setMonth(start.getMonth() - 3);
      break;
    case "6M":
      start.setMonth(start.getMonth() - 6);
      break;
    case "12M":
      start.setMonth(start.getMonth() - 12);
      break;
    default:
      return null;
  }
  return { start: start.toISOString(), end: now.toISOString() };
}

/** Group a flat sorted widget list into rows based on cumulative widths.
 *  Widgets in each row are normalized so their widths sum to exactly 12. */
function computeRows(widgets) {
  const sorted = [...widgets].sort((a, b) => a.position - b.position);
  const rows = [];
  let currentRow = [];
  let rowWidth = 0;
  for (const w of sorted) {
    const width = w.width || 12;
    if (rowWidth + width > 12 && currentRow.length > 0) {
      rows.push(currentRow);
      currentRow = [{ ...w, width }];
      rowWidth = width;
    } else {
      currentRow.push({ ...w, width });
      rowWidth += width;
    }
  }
  if (currentRow.length > 0) rows.push(currentRow);

  return rows;
}

// ---------------------------------------------------------------------------
// InlineEdit — click-to-edit text field
// ---------------------------------------------------------------------------
const InlineEdit = forwardRef(function InlineEdit(
  { value, onSave, placeholder, typographyProps, multiline },
  ref,
) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value || "");
  const inputRef = useRef(null);

  const startEdit = () => {
    setDraft(value || "");
    setEditing(true);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  useImperativeHandle(ref, () => ({ startEdit }), [value]);

  const save = () => {
    setEditing(false);
    const trimmed = draft.trim();
    if (trimmed !== (value || "").trim()) {
      onSave(trimmed);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !multiline) {
      e.preventDefault();
      save();
    }
    if (e.key === "Escape") {
      setEditing(false);
      setDraft(value || "");
    }
  };

  if (editing) {
    return (
      <ClickAwayListener onClickAway={save}>
        <InputBase
          inputRef={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          multiline={multiline}
          fullWidth
          placeholder={placeholder}
          sx={{
            ...typographyProps?.sx,
            fontSize: typographyProps?.sx?.fontSize || "inherit",
            fontWeight: typographyProps?.sx?.fontWeight || "inherit",
            border: "2px solid",
            borderColor: "primary.main",
            borderRadius: 1,
            px: 1,
            py: 0.5,
          }}
        />
      </ClickAwayListener>
    );
  }

  return (
    <Typography
      {...typographyProps}
      onClick={startEdit}
      sx={{
        cursor: "pointer",
        borderRadius: 1,
        px: 1,
        py: 0.5,
        border: "2px solid transparent",
        "&:hover": {
          border: "2px solid",
          borderColor: "divider",
        },
        transition: "border-color 0.15s",
        ...typographyProps?.sx,
      }}
    >
      {value || (
        <span style={{ opacity: 0.45 }}>{placeholder || "Click to edit"}</span>
      )}
    </Typography>
  );
});

// ---------------------------------------------------------------------------
// DropZone — droppable area that shows a blue indicator line when hovered
// ---------------------------------------------------------------------------
function DropZone({ id, direction = "vertical", isDragging }) {
  const { setNodeRef, isOver } = useDroppable({ id });

  if (direction === "horizontal") {
    return (
      <Box
        ref={setNodeRef}
        sx={{
          height: isDragging ? 24 : 4,
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          transition: "height 0.2s",
        }}
      >
        <Box
          sx={{
            height: 3,
            width: "100%",
            borderRadius: 2,
            bgcolor: isOver ? "primary.main" : "transparent",
            transition: "background-color 0.15s",
          }}
        />
      </Box>
    );
  }

  // Vertical — invisible when not dragging, expands during drag
  return (
    <Box
      ref={setNodeRef}
      sx={{
        width: isDragging ? 28 : 0,
        minHeight: isDragging ? 120 : 0,
        display: "flex",
        alignItems: "stretch",
        justifyContent: "center",
        flexShrink: 0,
        alignSelf: "stretch",
        transition: "width 0.2s",
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          width: 3,
          my: 2,
          borderRadius: 2,
          bgcolor: isOver ? "primary.main" : "transparent",
          transition: "background-color 0.15s",
        }}
      />
    </Box>
  );
}

// ---------------------------------------------------------------------------
// ResizeHandle — draggable divider between adjacent widgets in a row
// Resizes live as you drag, snapping to grid columns.
// ---------------------------------------------------------------------------
function ResizeHandle({
  leftWidget,
  rightWidget,
  containerWidth,
  onResizeEnd,
}) {
  const handleRef = useRef(null);
  const colWidth = containerWidth / 12;

  const handleMouseDown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const leftStart = leftWidget.width || 6;
    const rightStart = rightWidget.width || 6;
    const totalWidth = leftStart + rightStart;

    // Find the sibling DOM elements for live resizing
    const handleEl = handleRef.current;
    const leftEl = handleEl?.previousElementSibling;
    const rightEl = handleEl?.nextElementSibling;
    // Skip past the DropZone that sits between handle and next widget
    const actualRightEl = rightEl?.getAttribute?.("data-widget-id")
      ? rightEl
      : rightEl?.nextElementSibling;

    let lastCols = leftStart;

    const onMouseMove = (moveE) => {
      document.body.style.cursor = "col-resize";
      const deltaPixels = moveE.clientX - startX;
      const deltaCols = Math.round(deltaPixels / colWidth);
      const newLeft = Math.max(
        2,
        Math.min(totalWidth - 2, leftStart + deltaCols),
      );
      const newRight = totalWidth - newLeft;

      if (newLeft !== lastCols) {
        lastCols = newLeft;
        // Live update the flex of both widgets
        const leftPct = (newLeft / 12) * 100;
        const rightPct = (newRight / 12) * 100;
        if (leftEl) {
          leftEl.style.flex = `1 1 ${leftPct}%`;
          leftEl.style.maxWidth = `${leftPct}%`;
        }
        if (actualRightEl) {
          actualRightEl.style.flex = `1 1 ${rightPct}%`;
          actualRightEl.style.maxWidth = `${rightPct}%`;
        }
      }
    };

    const onMouseUp = (upE) => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      // Clear inline styles so React takes back control
      if (leftEl) {
        leftEl.style.flex = "";
        leftEl.style.maxWidth = "";
      }
      if (actualRightEl) {
        actualRightEl.style.flex = "";
        actualRightEl.style.maxWidth = "";
      }

      const deltaPixels = upE.clientX - startX;
      const deltaCols = Math.round(deltaPixels / colWidth);
      const newLeft = Math.max(
        2,
        Math.min(totalWidth - 2, leftStart + deltaCols),
      );
      const newRight = totalWidth - newLeft;
      if (newLeft !== leftStart) {
        onResizeEnd(leftWidget.id, newLeft, rightWidget.id, newRight);
      }
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  };

  return (
    <Box
      ref={handleRef}
      onMouseDown={handleMouseDown}
      sx={{
        width: 12,
        cursor: "col-resize",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        alignSelf: "stretch",
        mx: "-6px",
        zIndex: 2,
        position: "relative",
        "&:hover .resize-bar, &:active .resize-bar": { opacity: 1 },
      }}
    >
      <Box
        className="resize-bar"
        sx={{
          width: 3,
          height: "100%",
          borderRadius: 2,
          bgcolor: "primary.main",
          opacity: 0,
          transition: "opacity 0.15s",
        }}
      />
    </Box>
  );
}

// ---------------------------------------------------------------------------
// RowResizeHandle — a single horizontal bar below the entire row
// ---------------------------------------------------------------------------
function RowResizeHandle({ row, onRowResize }) {
  const handleMouseDown = (e) => {
    e.preventDefault();
    const startY = e.clientY;
    // Find the row container (parent of this handle)
    const rowEl = e.currentTarget.previousElementSibling;
    const cards = rowEl ? rowEl.querySelectorAll(".MuiCard-root") : [];
    const startHeight = cards.length > 0 ? cards[0].offsetHeight : 320;

    const onMouseMove = (moveE) => {
      const delta = moveE.clientY - startY;
      const newH = Math.max(MIN_WIDGET_HEIGHT, startHeight + delta);
      cards.forEach((card) => {
        card.style.height = `${newH}px`;
      });
      document.body.style.cursor = "row-resize";
    };

    const onMouseUp = (upE) => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      const delta = upE.clientY - startY;
      const newH = Math.max(MIN_WIDGET_HEIGHT, startHeight + delta);
      if (newH !== startHeight) {
        onRowResize(row, Math.round(newH));
      }
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  };

  return (
    <Box
      onMouseDown={handleMouseDown}
      sx={{
        width: "100%",
        height: 12,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        cursor: "row-resize",
        "&:hover .row-resize-bar": { opacity: 1 },
      }}
    >
      <Box
        className="row-resize-bar"
        sx={{
          width: 48,
          height: 4,
          borderRadius: 2,
          bgcolor: "text.disabled",
          opacity: 0,
          transition: "opacity 0.15s",
        }}
      />
    </Box>
  );
}

// ---------------------------------------------------------------------------
// DraggableWidgetCard — individual widget card with drag handle
// ---------------------------------------------------------------------------
function DraggableWidgetCard({
  widget,
  dashboardId,
  navigate,
  onMenuOpen,
  globalDateRange,
  _isDragActive,
  rowHeight,
  datePreset,
}) {
  const theme = useTheme();
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: widget.id,
    data: { widget },
  });

  const widgetHeight =
    rowHeight ||
    (widget.height && widget.height > 50
      ? widget.height
      : DEFAULT_WIDGET_HEIGHT);

  return (
    <Box
      ref={setNodeRef}
      data-widget-id={widget.id}
      sx={{
        flex: `1 1 ${((widget.width || 12) / 12) * 100}%`,
        maxWidth: `${((widget.width || 12) / 12) * 100}%`,
        minWidth: 0,
        px: "4px",
        opacity: isDragging ? 0.25 : 1,
        transition: "opacity 0.2s, flex 0.2s",
        position: "relative",
        "&:hover .widget-actions": { opacity: 1 },
        "&:hover .drag-handle": { opacity: 1 },
      }}
    >
      <Card
        variant="outlined"
        sx={{
          height: widgetHeight,
          display: "flex",
          flexDirection: "column",
          transition: "border-color 0.2s, box-shadow 0.2s",
          "&:hover": {
            borderColor: theme.palette.divider,
            boxShadow: theme.shadows[2],
          },
          overflow: "hidden",
        }}
      >
        <CardContent
          sx={{
            p: 2,
            "&:last-child": { pb: 2 },
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {/* Header row — entire bar is the drag activator */}
          <div
            {...attributes}
            {...listeners}
            style={{
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              marginBottom: 4,
              minHeight: 24,
              cursor: "grab",
            }}
          >
            <Iconify
              icon="mdi:drag"
              width={16}
              sx={{ color: "text.disabled", mr: 0.5, flexShrink: 0 }}
            />

            <Typography
              variant="subtitle2"
              fontWeight="fontWeightSemiBold"
              noWrap
              sx={{
                flex: 1,
                cursor: "pointer",
                "&:hover": { textDecoration: "underline" },
              }}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() =>
                navigate(
                  `/dashboard/dashboards/${dashboardId}/widget/${widget.id}${datePreset ? `?timePreset=${datePreset}` : ""}`,
                )
              }
            >
              {widget.name}
            </Typography>

            {/* Actions */}
            <Stack
              className="widget-actions"
              direction="row"
              spacing={0}
              onPointerDown={(e) => e.stopPropagation()}
              sx={{ opacity: 0, transition: "opacity 0.15s" }}
            >
              <Tooltip title="Edit">
                <IconButton
                  size="small"
                  onClick={() =>
                    navigate(
                      `/dashboard/dashboards/${dashboardId}/widget/${widget.id}${datePreset ? `?timePreset=${datePreset}` : ""}`,
                    )
                  }
                >
                  <Iconify icon="mdi:pencil-outline" width={16} />
                </IconButton>
              </Tooltip>
              <Tooltip title="More">
                <IconButton size="small" onClick={(e) => onMenuOpen(e, widget)}>
                  <Iconify icon="mdi:dots-vertical" width={16} />
                </IconButton>
              </Tooltip>
            </Stack>
          </div>

          {/* Chart */}
          <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
            <WidgetChart widget={widget} globalDateRange={globalDateRange} />
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// DragOverlayCard — compact preview shown while dragging
// ---------------------------------------------------------------------------
function DragOverlayCard({ widget }) {
  const theme = useTheme();
  return (
    <Card
      variant="outlined"
      sx={{
        width: 280,
        height: 120,
        opacity: 0.9,
        boxShadow: theme.shadows[16],
        pointerEvents: "none",
        overflow: "hidden",
      }}
    >
      <CardContent sx={{ p: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Iconify icon="mdi:drag" width={14} sx={{ color: "text.disabled" }} />
          <Typography variant="subtitle2" noWrap>
            {widget.name}
          </Typography>
        </Stack>
        <Box
          sx={{
            mt: 1,
            height: 60,
            borderRadius: 1,
            bgcolor: "action.hover",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Iconify
            icon="mdi:chart-line"
            width={24}
            sx={{ color: "text.disabled" }}
          />
        </Box>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------
export default function DashboardDetailView() {
  const navigate = useNavigate();
  const { dashboardId } = useParams();

  const { data: dashboard, isLoading } = useDashboardDetail(dashboardId);
  const updateDashboard = useUpdateDashboard();
  const updateWidget = useUpdateWidget();
  const deleteWidget = useDeleteWidget();
  const deleteDashboard = useDeleteDashboard();
  const reorderWidgets = useReorderWidgets();
  const duplicateWidget = useDuplicateWidget();
  const createWidget = useCreateWidget();

  // Global date filter
  const [datePreset, setDatePreset] = useState(null);
  const globalDateRange = useMemo(
    () => (datePreset ? getDateRange(datePreset) : null),
    [datePreset],
  );

  // Widget context menu
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [menuWidget, setMenuWidget] = useState(null);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);

  // Width submenu
  const [widthMenuAnchor, setWidthMenuAnchor] = useState(null);

  // Dashboard more menu
  const [dashMenuAnchor, setDashMenuAnchor] = useState(null);

  // Grid container ref (for measuring column widths during resize)
  const gridContainerRef = useRef(null);

  // Drag state
  const [activeWidget, setActiveWidget] = useState(null);

  // DnD sensors
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  );

  const widgets = useMemo(
    () =>
      (dashboard?.widgets || [])
        .slice()
        .sort((a, b) => a.position - b.position),
    [dashboard?.widgets],
  );

  const rows = useMemo(() => computeRows(widgets), [widgets]);

  // --- Handlers ---

  const titleEditRef = useRef(null);

  const handleNameSave = useCallback(
    (name) => {
      if (name) updateDashboard.mutate({ id: dashboardId, data: { name } });
    },
    [dashboardId, updateDashboard],
  );

  const handleDescSave = useCallback(
    (description) => {
      updateDashboard.mutate({ id: dashboardId, data: { description } });
    },
    [dashboardId, updateDashboard],
  );

  const handleDragStart = useCallback(
    (event) => {
      const w = widgets.find((w) => w.id === event.active.id);
      setActiveWidget(w || null);
    },
    [widgets],
  );

  const handleDragEnd = useCallback(
    (event) => {
      setActiveWidget(null);
      const { active, over } = event;
      if (!over) return;

      const draggedId = active.id;
      const dropId = String(over.id);

      // Parse drop zone ID
      // Formats:
      //   "gap-r{rowIdx}-{insertIdx}"  → insert before widget at insertIdx in row
      //   "gap-r{rowIdx}-end"          → insert after last widget in row
      //   "gap-row-{rowIdx}"           → new row before row rowIdx
      //   "gap-row-end"                → new row at the end

      // Build a mutable copy of rows (excluding the dragged widget)
      const draggedWidget = widgets.find((w) => w.id === draggedId);
      if (!draggedWidget) return;

      const rowsCopy = rows
        .map((row) => row.filter((w) => w.id !== draggedId))
        .filter((row) => row.length > 0);

      let targetRowIdx;
      let insertIdx;
      let isNewRow = false;

      if (dropId.startsWith("gap-row-end")) {
        // New row at the bottom
        isNewRow = true;
        targetRowIdx = rowsCopy.length;
      } else if (dropId.startsWith("gap-row-")) {
        // New row before rowIdx
        isNewRow = true;
        targetRowIdx = parseInt(dropId.replace("gap-row-", ""), 10);
        // Adjust targetRowIdx if the dragged widget was in an earlier row that collapsed
        const origRowIdx = rows.findIndex((r) =>
          r.some((w) => w.id === draggedId),
        );
        if (origRowIdx >= 0 && origRowIdx < targetRowIdx) {
          const origRow = rows[origRowIdx];
          if (origRow.length === 1) {
            // That row will collapse, shift target down
            targetRowIdx = Math.max(0, targetRowIdx - 1);
          }
        }
      } else if (dropId.startsWith("gap-r")) {
        const match = dropId.match(/^gap-r(\d+)-(.+)$/);
        if (!match) return;
        const rawRowIdx = parseInt(match[1], 10);
        const posStr = match[2];

        // Adjust rowIdx for collapsed rows
        // Map from original row index to rowsCopy index
        let adjustedRowIdx = rawRowIdx;
        const origRowIdx = rows.findIndex((r) =>
          r.some((w) => w.id === draggedId),
        );
        if (origRowIdx >= 0 && origRowIdx < rawRowIdx) {
          const origRow = rows[origRowIdx];
          if (origRow.length === 1) {
            adjustedRowIdx = Math.max(0, rawRowIdx - 1);
          }
        }

        targetRowIdx = Math.min(adjustedRowIdx, rowsCopy.length - 1);
        if (targetRowIdx < 0) {
          isNewRow = true;
          targetRowIdx = 0;
        } else {
          insertIdx =
            posStr === "end"
              ? rowsCopy[targetRowIdx].length
              : parseInt(posStr, 10);
          // Check if row can accept another widget (max 4 per row)
          if (rowsCopy[targetRowIdx].length >= 4) {
            // Can't fit, create new row instead
            isNewRow = true;
          }
        }
      } else {
        return; // Unknown drop zone
      }

      if (isNewRow) {
        // Insert a new row with just the dragged widget at full width
        rowsCopy.splice(targetRowIdx, 0, [{ ...draggedWidget, width: 12 }]);
      } else {
        // Insert into existing row and redistribute widths
        const row = rowsCopy[targetRowIdx];
        row.splice(insertIdx, 0, draggedWidget);
        const n = row.length;
        const perWidget = Math.floor(12 / n);
        const remainder = 12 - perWidget * n;
        for (let i = 0; i < row.length; i++) {
          row[i] = {
            ...row[i],
            width: perWidget + (i < remainder ? 1 : 0),
          };
        }
      }

      // Also redistribute the source row if it lost a widget
      for (const row of rowsCopy) {
        const totalW = row.reduce((s, w) => s + (w.width || 12), 0);
        if (totalW < 12 && row.length > 0 && row.length <= 4) {
          const n = row.length;
          const perWidget = Math.floor(12 / n);
          const remainder = 12 - perWidget * n;
          for (let i = 0; i < row.length; i++) {
            row[i] = {
              ...row[i],
              width: perWidget + (i < remainder ? 1 : 0),
            };
          }
        }
      }

      // Flatten rows into a new ordered list with positions
      const newOrder = rowsCopy.flat().map((w) => ({
        id: w.id,
        width: w.width || 12,
      }));

      reorderWidgets.mutate({ dashboardId, order: newOrder });
    },
    [dashboardId, widgets, rows, reorderWidgets],
  );

  const handleWidgetMenuOpen = useCallback((e, widget) => {
    e.stopPropagation();
    setMenuAnchor(e.currentTarget);
    setMenuWidget(widget);
  }, []);

  const closeWidgetMenu = () => {
    setMenuAnchor(null);
    setMenuWidget(null);
    setWidthMenuAnchor(null);
  };

  const handleDeleteWidget = () => {
    if (menuWidget) {
      deleteWidget.mutate({ dashboardId, widgetId: menuWidget.id });
    }
    closeWidgetMenu();
  };

  const handleDuplicateWidget = () => {
    if (menuWidget) {
      duplicateWidget.mutate({ dashboardId, widgetId: menuWidget.id });
    }
    closeWidgetMenu();
  };

  const handleWidthChange = (newWidth) => {
    if (menuWidget) {
      updateWidget.mutate({
        dashboardId,
        widgetId: menuWidget.id,
        data: { width: newWidth },
      });
    }
    closeWidgetMenu();
  };

  const handleDeleteDashboard = () => {
    setDashMenuAnchor(null);
    deleteDashboard.mutate(dashboardId, {
      onSuccess: () => navigate(paths.dashboard.dashboards.root),
    });
  };

  const handleRowResize = useCallback(
    (rowWidgets, newHeight) => {
      // Update height for all widgets in the row
      rowWidgets.forEach((w) => {
        updateWidget.mutate({
          dashboardId,
          widgetId: w.id,
          data: { height: newHeight },
        });
      });
    },
    [dashboardId, updateWidget],
  );

  const handleWidthResize = useCallback(
    (leftId, leftWidth, rightId, rightWidth) => {
      // Update both widgets' widths via reorder (preserves positions)
      const newOrder = widgets.map((w) => {
        if (w.id === leftId) return { id: w.id, width: leftWidth };
        if (w.id === rightId) return { id: w.id, width: rightWidth };
        return { id: w.id, width: w.width || 12 };
      });
      reorderWidgets.mutate({ dashboardId, order: newOrder });
    },
    [dashboardId, widgets, reorderWidgets],
  );

  const handleAddToRow = useCallback(
    async (rowIdx) => {
      const row = rows[rowIdx];
      if (!row || row.length >= 4) return;

      // Compute new widget width — redistribute evenly
      const newCount = row.length + 1;
      const perWidget = Math.floor(12 / newCount);
      const remainder = 12 - perWidget * newCount;

      // Compute position: just after the last widget in this row
      const lastInRow = row[row.length - 1];
      const lastPos = lastInRow?.position ?? 0;

      try {
        // Create a blank widget placed into this row
        const res = await createWidget.mutateAsync({
          dashboardId,
          data: {
            name: "Untitled widget",
            query_config: {},
            chart_config: {},
            width: perWidget,
            height: row[0]?.height || DEFAULT_WIDGET_HEIGHT,
            position: lastPos + 1,
          },
        });

        const newWidgetId = res.data?.result?.id;
        if (!newWidgetId) return;

        // Build updated order: redistribute widths for this row
        const newOrder = [];
        for (let ri = 0; ri < rows.length; ri++) {
          for (let wi = 0; wi < rows[ri].length; wi++) {
            const w = rows[ri][wi];
            if (ri === rowIdx) {
              const idx = wi;
              newOrder.push({
                id: w.id,
                width: perWidget + (idx < remainder ? 1 : 0),
              });
            } else {
              newOrder.push({ id: w.id, width: w.width || 12 });
            }
          }
          // Insert new widget at end of target row
          if (ri === rowIdx) {
            newOrder.push({
              id: newWidgetId,
              width: perWidget + (row.length < remainder ? 1 : 0),
            });
          }
        }

        await reorderWidgets.mutateAsync({ dashboardId, order: newOrder });

        // Navigate to edit the new widget
        navigate(`/dashboard/dashboards/${dashboardId}/widget/${newWidgetId}`);
      } catch (err) {
        // error handled silently
      }
    },
    [dashboardId, rows, createWidget, reorderWidgets, navigate],
  );

  // --- Render ---

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "60vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (!dashboard) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "60vh",
        }}
      >
        <Typography color="text.secondary">Dashboard not found</Typography>
      </Box>
    );
  }

  const containerWidth = gridContainerRef.current?.offsetWidth || 1200;

  return (
    <Box
      sx={{
        display: "flex",
        flex: 1,
        flexDirection: "column",
        bgcolor: "background.paper",
        minHeight: "100vh",
      }}
    >
      {/* ---- Top bar: breadcrumb + actions ---- */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ px: 3, pt: 2, pb: 0 }}
      >
        <Breadcrumbs
          separator={<Iconify icon="mdi:chevron-right" width={16} />}
        >
          <Link
            underline="hover"
            color="text.secondary"
            sx={{ cursor: "pointer", fontSize: "14px" }}
            onClick={() => navigate(paths.dashboard.dashboards.root)}
          >
            Dashboards
          </Link>
          <Typography color="text.primary" fontSize="14px">
            {dashboard.name}
          </Typography>
        </Breadcrumbs>

        <Stack direction="row" spacing={0.5} alignItems="center">
          <Tooltip title="Share this dashboard">
            <IconButton
              size="small"
              onClick={() => setShareDialogOpen(true)}
              sx={{ color: "text.secondary" }}
            >
              <Iconify
                icon="mdi:share-variant-outline"
                width={18}
              />
            </IconButton>
          </Tooltip>
          <Tooltip title="More options">
            <IconButton
              size="small"
              onClick={(e) => setDashMenuAnchor(e.currentTarget)}
            >
              <Iconify icon="mdi:dots-horizontal" width={20} />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      {/* ---- Global date filter bar ---- */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          px: 3,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexWrap: "wrap",
          gap: 0.5,
        }}
      >
        <Iconify
          icon="mdi:calendar-outline"
          width={18}
          sx={{ color: "text.secondary", mr: 0.5 }}
        />
        {DATE_PRESETS.filter((p) => p.value !== "custom").map((preset) => (
          <Chip
            key={preset.value}
            label={preset.label}
            size="small"
            variant={datePreset === preset.value ? "filled" : "outlined"}
            color={datePreset === preset.value ? "primary" : "default"}
            onClick={() =>
              setDatePreset(datePreset === preset.value ? null : preset.value)
            }
            sx={{
              fontWeight: 500,
              fontSize: "12px",
              height: 28,
              borderRadius: "6px",
            }}
          />
        ))}
        <Chip
          label="Default"
          size="small"
          variant={!datePreset ? "filled" : "outlined"}
          color={!datePreset ? "primary" : "default"}
          onClick={() => setDatePreset(null)}
          sx={{
            fontWeight: 500,
            fontSize: "12px",
            height: 28,
            borderRadius: "6px",
          }}
        />
      </Stack>

      {/* ---- Dashboard title & description (inline editable) ---- */}
      <Box sx={{ px: 3, pt: 2 }}>
        <InlineEdit
          ref={titleEditRef}
          value={dashboard.name}
          onSave={handleNameSave}
          placeholder="Untitled Dashboard"
          typographyProps={{
            variant: "h4",
            sx: {
              fontSize: "28px",
              fontWeight: 700,
              color: "text.primary",
              lineHeight: 1.3,
            },
          }}
        />
        <InlineEdit
          value={dashboard.description}
          onSave={handleDescSave}
          placeholder="+ Add description..."
          multiline
          typographyProps={{
            variant: "body2",
            sx: {
              color: dashboard.description ? "text.secondary" : "text.disabled",
              mt: 0.5,
              fontSize: "14px",
            },
          }}
        />
      </Box>

      {/* ---- Widgets grid with drag-and-drop ---- */}
      <Box
        ref={gridContainerRef}
        sx={{ px: 3, pt: 2, pb: 4, flex: 1, overflow: "visible" }}
      >
        {widgets.length === 0 ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "50vh",
              gap: 2,
            }}
          >
            <Iconify
              icon="mdi:chart-line"
              width={64}
              sx={{ color: "text.disabled" }}
            />
            <Typography variant="h6" color="text.secondary">
              No widgets yet
            </Typography>
            <Typography variant="body2" color="text.disabled">
              Add your first widget to start visualizing data
            </Typography>
            <Button
              variant="outlined"
              startIcon={<Iconify icon="mdi:plus" />}
              onClick={() =>
                navigate(
                  `/dashboard/dashboards/${dashboardId}/widget/new${datePreset ? `?timePreset=${datePreset}` : ""}`,
                )
              }
            >
              Add Widget
            </Button>
          </Box>
        ) : (
          <>
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragStart={handleDragStart}
              onDragEnd={handleDragEnd}
            >
              {rows.map((row, rowIdx) => {
                // Compute uniform row height (max of all widgets in this row)
                const rowHeight =
                  row.length > 1
                    ? Math.max(
                        ...row.map((w) =>
                          w.height && w.height > 50
                            ? w.height
                            : DEFAULT_WIDGET_HEIGHT,
                        ),
                      )
                    : undefined;

                return (
                  <React.Fragment key={rowIdx}>
                    {/* Horizontal drop zone between rows */}
                    <DropZone
                      id={`gap-row-${rowIdx}`}
                      direction="horizontal"
                      isDragging={!!activeWidget}
                    />

                    {/* Row wrapper with "+" button on left */}
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "stretch",
                        position: "relative",
                        ml: "-28px",
                        pl: "28px",
                        "&:hover .row-add-btn": { opacity: 1 },
                      }}
                    >
                      {/* Add-to-row button */}
                      {!activeWidget && row.length < 4 && (
                        <Tooltip title="Add widget to row" placement="left">
                          <IconButton
                            className="row-add-btn"
                            size="small"
                            onClick={() => handleAddToRow(rowIdx)}
                            sx={{
                              position: "absolute",
                              left: 6,
                              top: "50%",
                              transform: "translateY(-50%)",
                              width: 22,
                              height: 22,
                              minWidth: 22,
                              padding: 0,
                              borderRadius: "50%",
                              opacity: 0,
                              transition: "opacity 0.15s",
                              bgcolor: "background.paper",
                              border: "1px solid",
                              borderColor: "divider",
                              "&:hover": {
                                bgcolor: "action.hover",
                              },
                            }}
                          >
                            <Iconify icon="mdi:plus" width={14} />
                          </IconButton>
                        </Tooltip>
                      )}

                      {/* Row of widgets with vertical drop zones */}
                      <Box
                        sx={{
                          display: "flex",
                          alignItems: "stretch",
                          width: "100%",
                        }}
                      >
                        {row.map((widget, widgetIdx) => (
                          <React.Fragment key={widget.id}>
                            {/* Vertical drop zone before this widget */}
                            <DropZone
                              id={`gap-r${rowIdx}-${widgetIdx}`}
                              isDragging={!!activeWidget}
                            />

                            <DraggableWidgetCard
                              widget={widget}
                              dashboardId={dashboardId}
                              navigate={navigate}
                              onMenuOpen={handleWidgetMenuOpen}
                              globalDateRange={globalDateRange}
                              isDragActive={!!activeWidget}
                              rowHeight={rowHeight}
                              datePreset={datePreset}
                            />

                            {/* Resize handle between adjacent widgets */}
                            {!activeWidget && widgetIdx < row.length - 1 && (
                              <ResizeHandle
                                leftWidget={widget}
                                rightWidget={row[widgetIdx + 1]}
                                containerWidth={containerWidth}
                                onResizeEnd={handleWidthResize}
                              />
                            )}
                          </React.Fragment>
                        ))}

                        {/* Vertical drop zone after last widget in row */}
                        <DropZone
                          id={`gap-r${rowIdx}-end`}
                          isDragging={!!activeWidget}
                        />
                      </Box>
                    </Box>

                    {/* Row-level height resize handle */}
                    {!activeWidget && (
                      <RowResizeHandle
                        row={row}
                        onRowResize={handleRowResize}
                      />
                    )}
                  </React.Fragment>
                );
              })}

              {/* Horizontal drop zone at the very end */}
              <DropZone
                id="gap-row-end"
                direction="horizontal"
                isDragging={!!activeWidget}
              />

              {/* Drag overlay — follows cursor */}
              <DragOverlay dropAnimation={null}>
                {activeWidget ? (
                  <DragOverlayCard widget={activeWidget} />
                ) : null}
              </DragOverlay>
            </DndContext>

            {/* Add widget button below grid */}
            <Box sx={{ display: "flex", justifyContent: "center", mt: 3 }}>
              <Button
                variant="outlined"
                startIcon={<Iconify icon="mdi:plus" />}
                onClick={() =>
                  navigate(
                    `/dashboard/dashboards/${dashboardId}/widget/new${datePreset ? `?timePreset=${datePreset}` : ""}`,
                  )
                }
                sx={{ borderStyle: "dashed" }}
              >
                Add Widget
              </Button>
            </Box>
          </>
        )}
      </Box>

      {/* ---- Widget context menu ---- */}
      <Menu
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={() => {
          if (!widthMenuAnchor) closeWidgetMenu();
        }}
        slotProps={{ paper: { sx: { minWidth: 180 } } }}
      >
        <MenuItem
          onClick={() => {
            if (menuWidget) {
              navigate(
                `/dashboard/dashboards/${dashboardId}/widget/${menuWidget.id}`,
              );
            }
            closeWidgetMenu();
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:pencil-outline" width={18} />
          </ListItemIcon>
          <ListItemText>Edit</ListItemText>
        </MenuItem>
        <MenuItem onClick={handleDuplicateWidget}>
          <ListItemIcon>
            <Iconify icon="mdi:content-copy" width={18} />
          </ListItemIcon>
          <ListItemText>Duplicate</ListItemText>
        </MenuItem>
        <MenuItem onClick={(e) => setWidthMenuAnchor(e.currentTarget)}>
          <ListItemIcon>
            <Iconify icon="mdi:resize" width={18} />
          </ListItemIcon>
          <ListItemText>Resize Width</ListItemText>
          <Iconify icon="mdi:chevron-right" width={16} sx={{ ml: 1 }} />
        </MenuItem>
        <Divider />
        <MenuItem onClick={handleDeleteWidget} sx={{ color: "error.main" }}>
          <ListItemIcon>
            <Iconify
              icon="mdi:delete-outline"
              width={18}
              sx={{ color: "error.main" }}
            />
          </ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>

      {/* Width submenu */}
      <Menu
        anchorEl={widthMenuAnchor}
        open={Boolean(widthMenuAnchor)}
        onClose={closeWidgetMenu}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
      >
        {WIDTH_OPTIONS.map((opt) => (
          <MenuItem
            key={opt.value}
            selected={menuWidget?.width === opt.value}
            onClick={() => handleWidthChange(opt.value)}
          >
            <ListItemIcon>
              <Iconify icon={opt.icon} width={18} />
            </ListItemIcon>
            <ListItemText>{opt.label}</ListItemText>
          </MenuItem>
        ))}
      </Menu>

      {/* ---- Dashboard more menu ---- */}
      <Menu
        anchorEl={dashMenuAnchor}
        open={Boolean(dashMenuAnchor)}
        onClose={() => setDashMenuAnchor(null)}
        slotProps={{ paper: { sx: { minWidth: 180 } } }}
      >
        <MenuItem
          onClick={() => {
            setDashMenuAnchor(null);
            window.scrollTo({ top: 0, behavior: "smooth" });
            setTimeout(() => titleEditRef.current?.startEdit(), 300);
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:pencil-outline" width={18} />
          </ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>
        <MenuItem
          onClick={() => {
            setDashMenuAnchor(null);
            navigate(`/dashboard/dashboards/${dashboardId}/widget/new`);
          }}
        >
          <ListItemIcon>
            <Iconify icon="mdi:plus" width={18} />
          </ListItemIcon>
          <ListItemText>Add Widget</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem onClick={handleDeleteDashboard} sx={{ color: "error.main" }}>
          <ListItemIcon>
            <Iconify
              icon="mdi:delete-outline"
              width={18}
              sx={{ color: "error.main" }}
            />
          </ListItemIcon>
          <ListItemText>Delete Dashboard</ListItemText>
        </MenuItem>
      </Menu>

      <ShareDialog
        open={shareDialogOpen}
        onClose={() => setShareDialogOpen(false)}
        resourceType="dashboard"
        resourceId={dashboardId}
        fallbackShareUrl={window.location.href}
      />
    </Box>
  );
}
