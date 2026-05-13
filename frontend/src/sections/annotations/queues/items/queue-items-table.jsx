import PropTypes from "prop-types";
import React, { useState, useMemo, useCallback } from "react";
import {
  Avatar,
  AvatarGroup,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  Popover,
  Skeleton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { AgGridReact } from "ag-grid-react";
import Iconify from "src/components/iconify";
import { getInitials } from "../annotation-queue-table";
import { isQueueAnnotatorRole } from "../constants";
import SourceBadge from "./source-badge";
import ItemStatusBadge from "./item-status-badge";
import { fToNow } from "src/utils/format-time";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import "src/styles/clean-data-table.css";

const SkeletonCell = () => (
  <Box sx={{ display: "flex", alignItems: "center", height: "100%", px: 1 }}>
    <Skeleton variant="rounded" width="100%" height={20} />
  </Box>
);

const SKELETON_ROWS = Array.from({ length: 5 }, (_, i) => ({
  id: `skeleton-${i}`,
  _skeleton: true,
}));

const REVIEW_COLORS = {
  pending_review: "warning",
  approved: "success",
  rejected: "error",
  resubmitted: "info",
};

function getPreviewText(preview) {
  if (!preview) return "—";
  if (preview.deleted) return "Source deleted";
  if (preview.error) return preview.error;

  switch (preview.type) {
    case "dataset_row":
      return `${preview.dataset_name || "Dataset"} - Row ${preview.row_order ?? ""}`;
    case "trace":
      return preview.name || preview.input_preview || "Trace";
    case "observation_span":
      return `${preview.name || "Span"} (${preview.observation_type || ""})`;
    case "prototype_run":
      return `${preview.name || "Run"} - ${preview.model || ""}`;
    case "call_execution":
      return `${preview.simulation_call_type || "Call"} - ${preview.status || ""}`;
    default:
      return preview.type || "—";
  }
}

// ---------------------------------------------------------------------------
// Cell renderers
// ---------------------------------------------------------------------------
function SourceCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <SourceBadge sourceType={data.source_type} />
    </Box>
  );
}

SourceCellRenderer.propTypes = {
  data: PropTypes.object,
};

function PreviewCellRenderer({ data }) {
  if (!data) return null;
  const preview = data.source_preview;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2" noWrap sx={{ maxWidth: 400 }}>
        {getPreviewText(preview)}
      </Typography>
    </Box>
  );
}

PreviewCellRenderer.propTypes = {
  data: PropTypes.object,
};

function StatusCellRenderer({ data }) {
  if (!data) return null;
  const workflowStatus =
    data.workflow_status ||
    (data.review_status === "pending_review" ? "in_review" : data.status);
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <ItemStatusBadge status={workflowStatus} />
        {data.reserved_by && (
          <Tooltip title={`Reserved by ${data.reserved_by_name || "someone"}`}>
            <Iconify
              icon="mingcute:lock-fill"
              width={16}
              sx={{ color: "warning.main" }}
            />
          </Tooltip>
        )}
      </Stack>
    </Box>
  );
}

StatusCellRenderer.propTypes = {
  data: PropTypes.object,
};

function AssignedCellRenderer({ data, context }) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");
  const [localSelected, setLocalSelected] = useState(new Set());
  if (!data) return null;

  const assignedUsers = data.assigned_users || [];
  const annotators = (context?.annotators || []).filter(isQueueAnnotatorRole);
  const assignItems = context?.onAssign;
  const isAutoAssign = Boolean(context?.autoAssign);
  const canAssign = Boolean(assignItems) && !isAutoAssign;

  if (isAutoAssign) {
    const title =
      annotators.length > 0
        ? annotators.map((a) => a.name || a.email).join(", ")
        : "All annotators";
    return (
      <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
        <Tooltip title={title}>
          <Chip
            label={
              annotators.length === 1
                ? annotators[0].name || annotators[0].email
                : "All annotators"
            }
            size="small"
            variant="outlined"
            color="primary"
            sx={{ height: 24, fontSize: 12 }}
          />
        </Tooltip>
      </Box>
    );
  }

  const handleClick = (e) => {
    if (!canAssign) return;
    e.stopPropagation();
    setLocalSelected(new Set(assignedUsers.map((u) => String(u.id))));
    setAnchorEl(e.currentTarget);
    setSearch("");
  };

  const handleClose = () => {
    setAnchorEl(null);
    setSearch("");
  };

  const handleToggleUser = (userId) => {
    const uid = String(userId);
    setLocalSelected((prev) => {
      const next = new Set(prev);
      if (next.has(uid)) next.delete(uid);
      else next.add(uid);
      return next;
    });
  };

  const handleApply = () => {
    const selectedUserIds = Array.from(localSelected);
    assignItems?.({
      itemIds: [data.id],
      userIds: selectedUserIds,
      action: "set",
    });
    handleClose();
    if (context?.gridRef?.current) {
      setTimeout(() => {
        context.gridRef.current.api.refreshCells({
          columns: ["assignedTo"],
          force: true,
        });
      }, 1000);
    }
  };

  const filtered = annotators.filter(
    (a) =>
      !search ||
      (a.name || "").toLowerCase().includes(search.toLowerCase()) ||
      (a.email || "").toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      {assignedUsers.length > 0 ? (
        <Tooltip
          title={
            <Stack spacing={0.25} sx={{ py: 0.25 }}>
              {assignedUsers.map((u) => (
                <Typography key={u.id} variant="caption">
                  {u.name || "Unnamed"}
                </Typography>
              ))}
            </Stack>
          }
          arrow
          placement="top"
        >
          <AvatarGroup
            max={3}
            onClick={canAssign ? handleClick : undefined}
            sx={{
              cursor: canAssign ? "pointer" : "default",
              "& .MuiAvatar-root": {
                width: 28,
                height: 28,
                fontSize: 12,
                fontWeight: 600,
                bgcolor: "primary.main",
                color: "primary.contrastText",
                border: "2px solid",
                borderColor: "background.paper",
              },
            }}
          >
            {assignedUsers.map((u) => (
              <Avatar key={u.id}>{getInitials(u.name, null)}</Avatar>
            ))}
          </AvatarGroup>
        </Tooltip>
      ) : (
        <Chip
          label="+ Assign"
          size="small"
          variant="outlined"
          color="primary"
          onClick={canAssign ? handleClick : undefined}
          sx={{
            cursor: canAssign ? "pointer" : "default",
            height: 24,
            fontSize: 12,
            borderStyle: "dashed",
            "&:hover": canAssign
              ? {
                  bgcolor: (theme) => alpha(theme.palette.primary.main, 0.12),
                }
              : {},
          }}
        />
      )}
      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={handleClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 220 } } }}
      >
        <Box sx={{ px: 0.75, py: 0.75 }}>
          <TextField
            size="small"
            placeholder="Search..."
            fullWidth
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            autoFocus
          />
        </Box>
        <List dense disablePadding sx={{ height: 200, overflow: "auto" }}>
          {filtered.map((a) => {
            const uid = String(a.user_id || a.id);
            const isChecked = localSelected.has(uid);
            return (
              <ListItemButton
                key={uid}
                dense
                onClick={(e) => {
                  e.stopPropagation();
                  handleToggleUser(a.user_id || a.id);
                }}
                sx={{ px: 0.75, py: 0.25 }}
              >
                <Checkbox
                  size="small"
                  checked={isChecked}
                  sx={{ p: 0.25, mr: 0.5 }}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => handleToggleUser(a.user_id || a.id)}
                />
                <ListItemText
                  primary={a.name || a.email}
                  secondary={a.name ? a.email : undefined}
                  primaryTypographyProps={{ variant: "body2", noWrap: true }}
                  secondaryTypographyProps={{
                    variant: "caption",
                    noWrap: true,
                  }}
                />
              </ListItemButton>
            );
          })}
          {filtered.length === 0 && (
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ px: 1, py: 1, display: "block", textAlign: "center" }}
            >
              No annotators found
            </Typography>
          )}
        </List>
        <Box
          sx={{
            px: 0.75,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
            display: "flex",
            gap: 0.75,
          }}
        >
          <Button
            size="small"
            variant="outlined"
            color="primary"
            fullWidth
            onClick={handleClose}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            fullWidth
            onClick={handleApply}
          >
            Apply
          </Button>
        </Box>
      </Popover>
    </Box>
  );
}

AssignedCellRenderer.propTypes = {
  data: PropTypes.object,
  context: PropTypes.object,
};

function ReviewCellRenderer({ data }) {
  if (!data) return null;
  const reviewStatus = data.review_status;
  if (!reviewStatus) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
        <Typography variant="caption" color="text.disabled">
          —
        </Typography>
      </Box>
    );
  }
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Chip
        label={reviewStatus.replace("_", " ")}
        size="small"
        color={REVIEW_COLORS[reviewStatus] || "default"}
        sx={{ height: 22, fontSize: 11 }}
      />
    </Box>
  );
}

ReviewCellRenderer.propTypes = {
  data: PropTypes.object,
};

function AddedCellRenderer({ data }) {
  if (!data) return null;
  const date = data.created_at;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2" color="text.secondary">
        {date ? fToNow(date) : "—"}
      </Typography>
    </Box>
  );
}

AddedCellRenderer.propTypes = {
  data: PropTypes.object,
};

function ActionsCellRenderer({ data, context }) {
  if (!data || !context?.onRemoveConfirm) return null;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
      }}
    >
      <Tooltip title="Remove item">
        <IconButton
          size="small"
          onClick={(e) => {
            e.stopPropagation();
            context?.onRemoveConfirm(data);
          }}
          sx={{
            opacity: 0,
            color: "text.disabled",
            ".ag-row:hover &": { opacity: 1 },
            "&:hover": { color: "error.main" },
          }}
        >
          <Iconify icon="mingcute:close-line" width={16} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

ActionsCellRenderer.propTypes = {
  data: PropTypes.object,
  context: PropTypes.object,
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function QueueItemsTable({
  data = [],
  loading,
  totalCount,
  hasNextPage,
  isFetchingNextPage,
  onLoadMore,
  selectedIds,
  onSelectToggle,
  onSelectAll,
  onRemove,
  onItemClick,
  annotators = [],
  onAssign,
  autoAssign = false,
  gridRef = null,
  canManageItems = true,
  addedSortDirection = "desc",
  onAddedSortChange,
}) {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);

  const [removeTarget, setRemoveTarget] = useState(null);

  const columnDefs = useMemo(
    () =>
      [
        {
          field: "source_type",
          headerName: "Source",
          flex: 1,
          minWidth: 130,
          cellRenderer: loading ? SkeletonCell : SourceCellRenderer,
        },
        {
          field: "preview",
          headerName: "Preview",
          flex: 2.5,
          minWidth: 250,
          cellRenderer: loading ? SkeletonCell : PreviewCellRenderer,
        },
        {
          field: "status",
          headerName: "Status",
          flex: 1,
          minWidth: 130,
          cellRenderer: loading ? SkeletonCell : StatusCellRenderer,
        },
        {
          field: "assignedTo",
          headerName: "Assigned To",
          flex: 1,
          minWidth: 130,
          cellRenderer: loading ? SkeletonCell : AssignedCellRenderer,
          valueGetter: (params) => {
            const users = params.data?.assigned_users || [];
            return users.map((u) => u.id).join(",");
          },
        },
        {
          field: "review_status",
          headerName: "Review",
          flex: 0.8,
          minWidth: 110,
          cellRenderer: loading ? SkeletonCell : ReviewCellRenderer,
        },
        {
          field: "created_at",
          headerName: "Added",
          flex: 1,
          minWidth: 140,
          sortable: true,
          sort: addedSortDirection,
          sortingOrder: ["desc", "asc"],
          cellRenderer: loading ? SkeletonCell : AddedCellRenderer,
        },
        {
          field: "actions",
          headerName: "",
          width: 60,
          maxWidth: 60,
          cellRenderer: loading ? SkeletonCell : ActionsCellRenderer,
          sortable: false,
          resizable: false,
        },
      ].filter((column) => canManageItems || column.field !== "actions"),
    [loading, canManageItems, addedSortDirection],
  );

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      sortable: false,
      resizable: false,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
    }),
    [],
  );

  const selectionColumnDef = useMemo(
    () => ({ pinned: true, lockPinned: true }),
    [],
  );

  const gridContext = useMemo(
    () => ({
      onRemoveConfirm: canManageItems ? (item) => setRemoveTarget(item) : null,
      annotators,
      onAssign,
      autoAssign,
      gridRef,
    }),
    [annotators, onAssign, autoAssign, gridRef, canManageItems],
  );

  const onCellClicked = useCallback(
    (event) => {
      if (!event?.data) return;
      const colId = event.column?.getColId();
      if (
        colId === "actions" ||
        colId === "ag-Grid-SelectionColumn" ||
        colId === "assignedTo"
      )
        return;
      onItemClick?.(event.data);
    },
    [onItemClick],
  );

  const onSelectionChanged = useCallback(
    (event) => {
      const selectedNodes = event.api.getSelectedNodes();
      const ids = selectedNodes.map((n) => n.data?.id).filter(Boolean);
      const allIds = (data || []).map((d) => d.id);
      const currentSet = new Set(ids);

      // Sync with parent's selectedIds
      if (allIds.length === 0) return;

      // Check if all items are selected
      if (ids.length === allIds.length && selectedIds.size !== allIds.length) {
        onSelectAll?.();
      } else if (ids.length === 0 && selectedIds.size > 0) {
        // Deselect all - clear each selected item
        for (const id of selectedIds) {
          onSelectToggle?.(id);
        }
      } else {
        // Partial selection - sync individual items
        for (const id of allIds) {
          const inGrid = currentSet.has(id);
          const inParent = selectedIds.has(id);
          // Only toggle if state differs
          if (inGrid !== inParent) {
            onSelectToggle?.(id);
          }
        }
      }
    },
    [data, selectedIds, onSelectAll, onSelectToggle],
  );

  const onSortChanged = useCallback(
    (event) => {
      const addedColumn = event.api
        .getColumnState()
        .find((column) => column.colId === "created_at");
      onAddedSortChange?.(addedColumn?.sort === "asc" ? "asc" : "desc");
    },
    [onAddedSortChange],
  );

  const getRowId = useCallback((params) => params.data?.id, []);

  const CustomNoRowsOverlay = useCallback(
    () => (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <Typography variant="body2" color="text.secondary">
          No items match your filters
        </Typography>
      </Box>
    ),
    [],
  );

  const handleConfirmRemove = () => {
    if (removeTarget && onRemove) {
      onRemove(removeTarget);
      setRemoveTarget(null);
    }
  };

  return (
    <>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          overflow: "hidden",
        }}
      >
        <Box sx={{ flex: 1, minHeight: 0, overscrollBehavior: "none" }}>
          <AgGridReact
            ref={gridRef}
            theme={agTheme}
            rowData={loading ? SKELETON_ROWS : data}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            context={gridContext}
            rowHeight={52}
            headerHeight={42}
            pagination={false}
            animateRows={false}
            rowSelection={canManageItems ? { mode: "multiRow" } : undefined}
            selectionColumnDef={selectionColumnDef}
            suppressRowClickSelection={canManageItems}
            rowStyle={{ cursor: onItemClick ? "pointer" : "default" }}
            onCellClicked={onCellClicked}
            onSelectionChanged={canManageItems ? onSelectionChanged : undefined}
            onSortChanged={onSortChanged}
            getRowId={getRowId}
            noRowsOverlayComponent={CustomNoRowsOverlay}
            onBodyScroll={(e) => {
              if (!hasNextPage || isFetchingNextPage) return;
              const { bottom } = e.api.getVerticalPixelRange();
              const totalHeight = e.api.getDisplayedRowCount() * 52;
              if (bottom >= totalHeight - 200) {
                onLoadMore?.();
              }
            }}
          />
        </Box>

        {/* Footer status */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            py: 1.5,
            px: 2,
            fontSize: 13,
            color: "text.secondary",
            flexShrink: 0,
          }}
        >
          <Typography variant="caption" color="text.secondary">
            {data.length} of {totalCount} items
          </Typography>
          {isFetchingNextPage && (
            <Typography variant="caption" color="text.disabled">
              Loading more...
            </Typography>
          )}
        </Box>
      </Box>

      {/* Remove confirmation dialog */}
      <Dialog
        open={Boolean(removeTarget)}
        onClose={() => setRemoveTarget(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Remove Item</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to remove this item from the queue? This
            action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRemoveTarget(null)}>Cancel</Button>
          <Button
            onClick={handleConfirmRemove}
            color="error"
            variant="contained"
          >
            Remove
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

QueueItemsTable.propTypes = {
  data: PropTypes.array,
  loading: PropTypes.bool,
  totalCount: PropTypes.number,
  hasNextPage: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  onLoadMore: PropTypes.func,
  selectedIds: PropTypes.object.isRequired,
  onSelectToggle: PropTypes.func,
  onSelectAll: PropTypes.func,
  onRemove: PropTypes.func,
  onItemClick: PropTypes.func,
  annotators: PropTypes.array,
  onAssign: PropTypes.func,
  autoAssign: PropTypes.bool,
  gridRef: PropTypes.object,
  canManageItems: PropTypes.bool,
  addedSortDirection: PropTypes.oneOf(["asc", "desc"]),
  onAddedSortChange: PropTypes.func,
};
