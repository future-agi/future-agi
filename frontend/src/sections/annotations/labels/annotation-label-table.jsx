import PropTypes from "prop-types";
import { useState, useMemo, useCallback, useRef } from "react";
import {
  Box,
  Chip,
  IconButton,
  MenuItem,
  Popover,
  Skeleton,
  Tooltip,
  Typography,
  Select,
  Stack,
} from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { fDate } from "src/utils/format-time";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import "src/styles/clean-data-table.css";
import { getAnnotationLabelUsageCount } from "./annotation-label-usage";

const SkeletonCell = () => (
  <Box sx={{ display: "flex", alignItems: "center", height: "100%", px: 1 }}>
    <Skeleton variant="rounded" width="100%" height={20} />
  </Box>
);

const SKELETON_ROWS = Array.from({ length: 5 }, (_, i) => ({
  id: `skeleton-${i}`,
  _skeleton: true,
}));

const TYPE_COLORS = {
  categorical: "primary",
  numeric: "success",
  text: "default",
  star: "warning",
  thumbs_up_down: "secondary",
};

const TYPE_LABELS = {
  categorical: "Categorical",
  numeric: "Numeric",
  text: "Text",
  star: "Star Rating",
  thumbs_up_down: "Thumbs Up/Down",
};

// ---------------------------------------------------------------------------
// Cell renderers
// ---------------------------------------------------------------------------
function NameCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2" fontWeight={600} noWrap>
        {data.name}
      </Typography>
    </Box>
  );
}

function TypeCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Chip
        label={TYPE_LABELS[data.type] || data.type}
        color={TYPE_COLORS[data.type] || "default"}
        size="small"
        variant="soft"
      />
    </Box>
  );
}

function DescriptionCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Tooltip title={data.description || ""} placement="top">
        <Typography variant="body2" color="text.secondary" noWrap>
          {data.description || "-"}
        </Typography>
      </Tooltip>
    </Box>
  );
}

function UsedInCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2">
        {getAnnotationLabelUsageCount(data)}
      </Typography>
    </Box>
  );
}

function CreatedCellRenderer({ data }) {
  if (!data) return null;
  const date = data.created_at;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2" color="text.secondary">
        {date ? fDate(date) : "-"}
      </Typography>
    </Box>
  );
}

function ActionsCellRenderer({ data, context }) {
  if (!data) return null;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
      }}
    >
      <IconButton
        size="small"
        aria-label={
          context?.archivedView ? "Restore label actions" : "Label actions"
        }
        onClick={(e) => {
          e.stopPropagation();
          context?.onOpenMenu(e, data);
        }}
      >
        <Iconify icon="eva:more-vertical-fill" />
      </IconButton>
    </Box>
  );
}

const cellDataPropTypes = { data: PropTypes.object };
const cellContextPropTypes = {
  data: PropTypes.object,
  context: PropTypes.object,
};

NameCellRenderer.propTypes = cellDataPropTypes;
TypeCellRenderer.propTypes = cellDataPropTypes;
DescriptionCellRenderer.propTypes = cellDataPropTypes;
UsedInCellRenderer.propTypes = cellDataPropTypes;
CreatedCellRenderer.propTypes = cellDataPropTypes;
ActionsCellRenderer.propTypes = cellContextPropTypes;

// ---------------------------------------------------------------------------
// Main table component
// ---------------------------------------------------------------------------
AnnotationLabelTable.propTypes = {
  data: PropTypes.array,
  loading: PropTypes.bool,
  page: PropTypes.number,
  rowsPerPage: PropTypes.number,
  totalCount: PropTypes.number,
  onPageChange: PropTypes.func,
  onRowsPerPageChange: PropTypes.func,
  onEdit: PropTypes.func,
  onDuplicate: PropTypes.func,
  onArchive: PropTypes.func,
  onRestore: PropTypes.func,
  archivedView: PropTypes.bool,
};

export default function AnnotationLabelTable({
  data = [],
  loading,
  page,
  rowsPerPage,
  totalCount,
  onPageChange,
  onRowsPerPageChange,
  onEdit,
  onDuplicate,
  onArchive,
  onRestore,
  archivedView = false,
}) {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const gridRef = useRef(null);
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [menuRow, setMenuRow] = useState(null);

  const handleOpenMenu = useCallback((event, row) => {
    setMenuAnchor(event.currentTarget);
    setMenuRow(row);
  }, []);

  const handleCloseMenu = () => {
    setMenuAnchor(null);
    setMenuRow(null);
  };

  const handleAction = (action) => {
    const row = menuRow;
    handleCloseMenu();
    if (action === "edit") onEdit?.(row);
    else if (action === "duplicate") onDuplicate?.(row);
    else if (action === "archive") onArchive?.(row);
    else if (action === "restore") onRestore?.(row);
  };

  const columnDefs = useMemo(
    () => [
      {
        field: "name",
        headerName: "Name",
        flex: 2,
        minWidth: 200,
        cellRenderer: loading ? SkeletonCell : NameCellRenderer,
      },
      {
        field: "type",
        headerName: "Type",
        flex: 1,
        minWidth: 140,
        cellRenderer: loading ? SkeletonCell : TypeCellRenderer,
      },
      {
        field: "description",
        headerName: "Description",
        flex: 2,
        minWidth: 180,
        cellRenderer: loading ? SkeletonCell : DescriptionCellRenderer,
      },
      {
        field: "annotation_count",
        headerName: "Used In",
        flex: 0.8,
        minWidth: 100,
        cellRenderer: loading ? SkeletonCell : UsedInCellRenderer,
      },
      {
        field: "created_at",
        headerName: "Created",
        flex: 1,
        minWidth: 130,
        cellRenderer: loading ? SkeletonCell : CreatedCellRenderer,
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
    ],
    [loading],
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

  const gridContext = useMemo(
    () => ({ onOpenMenu: handleOpenMenu, archivedView }),
    [handleOpenMenu, archivedView],
  );

  const onCellClicked = useCallback(
    (event) => {
      if (!event?.data) return;
      if (archivedView) return;
      if (event.column?.getColId() === "actions") return;
      onEdit?.(event.data);
    },
    [archivedView, onEdit],
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
          No labels match your filters.
        </Typography>
      </Box>
    ),
    [],
  );

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
        <Box sx={{ flex: 1 }}>
          <AgGridReact
            ref={gridRef}
            theme={agTheme}
            rowData={loading ? SKELETON_ROWS : data}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            context={gridContext}
            rowHeight={42}
            headerHeight={42}
            pagination={false}
            animateRows={false}
            suppressRowClickSelection
            rowStyle={{ cursor: "pointer" }}
            onCellClicked={onCellClicked}
            getRowId={getRowId}
            noRowsOverlayComponent={CustomNoRowsOverlay}
          />
        </Box>

        {/* Pagination */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{
            p: 2,
            borderTop: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
            gap: 2,
          }}
        >
          <Stack gap={1} direction="row" alignItems="center">
            <Typography
              typography="body2"
              color="text.primary"
              fontWeight="fontWeightRegular"
            >
              Results per page
            </Typography>
            <Select
              size="small"
              value={rowsPerPage}
              onChange={(e) =>
                onRowsPerPageChange?.(parseInt(e.target.value, 10))
              }
              sx={{ height: 36, bgcolor: "background.paper" }}
            >
              {[10, 25, 50].map((size) => (
                <MenuItem key={size} value={size}>
                  {size}
                </MenuItem>
              ))}
            </Select>
          </Stack>

          <Stack direction="row" alignItems="center" gap={2}>
            <Typography variant="body2" color="text.secondary">
              {totalCount > 0
                ? `${page * rowsPerPage + 1}–${Math.min((page + 1) * rowsPerPage, totalCount)} of ${totalCount}`
                : "0 of 0"}
            </Typography>
            <Stack direction="row" gap={0.5}>
              <IconButton
                size="small"
                disabled={page === 0}
                onClick={() => onPageChange?.(page - 1)}
                sx={{
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Iconify
                  icon="octicon:chevron-left-24"
                  width={18}
                  height={18}
                  sx={{ path: { strokeWidth: 1.5 } }}
                />
              </IconButton>
              <IconButton
                size="small"
                disabled={(page + 1) * rowsPerPage >= totalCount}
                onClick={() => onPageChange?.(page + 1)}
                sx={{
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Iconify
                  icon="octicon:chevron-right-24"
                  width={18}
                  height={18}
                  sx={{ path: { strokeWidth: 1.5 } }}
                />
              </IconButton>
            </Stack>
          </Stack>
        </Stack>
      </Box>

      {/* Actions popover menu */}
      <Popover
        open={Boolean(menuAnchor)}
        anchorEl={menuAnchor}
        onClose={handleCloseMenu}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <Box sx={{ py: 0.5 }}>
          <MenuItem
            onClick={() => handleAction(archivedView ? "restore" : "edit")}
          >
            {archivedView ? (
              <>
                <Iconify
                  icon="solar:archive-up-bold"
                  width={18}
                  sx={{ mr: 1 }}
                />
                Restore
              </>
            ) : (
              <>
                <SvgColor
                  src="/assets/icons/ic_edit.svg"
                  sx={{ width: 18, height: 18, mr: 1 }}
                />
                Edit
              </>
            )}
          </MenuItem>
          {!archivedView && (
            <MenuItem onClick={() => handleAction("duplicate")}>
              <SvgColor
                src="/assets/icons/ic_duplicate.svg"
                sx={{ width: 18, height: 18, mr: 1 }}
              />
              Duplicate
            </MenuItem>
          )}
          {!archivedView && (
            <MenuItem
              onClick={() => handleAction("archive")}
              sx={{ color: "error.main" }}
            >
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                sx={{ width: 18, height: 18, mr: 1, color: "error.main" }}
              />
              Archive
            </MenuItem>
          )}
        </Box>
      </Popover>
    </>
  );
}
