/* eslint-disable react/prop-types */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DataGrid } from "@mui/x-data-grid";
import { alpha, Box, Stack, useTheme } from "@mui/material";
import { toTanstackState } from "src/sections/common/ux/oneview/columnLayout";
import HeaderMenu, {
  HeaderMenuButton,
  isMenuKey,
  useHeaderMenu,
} from "src/sections/common/ux/oneview/HeaderMenu";
import { hoverSelectClass } from "src/sections/common/ux/oneview/useHoverSelection";
import {
  useGroupColumns,
  useGroupRows,
} from "src/sections/common/ListFilter/useGroupedGrid";

const NO_LAYOUT = { columns: [], catalog: [] };

/** `ids` reordered: the known ids take the slots known ids held, in
 * `order`; unknown ids keep their place. Pure. */
function reorderKnown(ids, order) {
  const rank = new Map(order.map((id, i) => [id, i]));
  const known = ids.filter((id) => rank.has(id));
  known.sort((a, b) => rank.get(a) - rank.get(b));
  let k = 0;
  return ids.map((id) => (rank.has(id) ? known[k++] : id));
}

/**
 * DataTable — thin wrapper on MUI X DataGrid (already in the project).
 * Server-side pagination, sorting, selection. No virtualization white flash.
 */
export default function DataTable({
  columns,
  data,
  isLoading = false,
  rowCount = 0,
  sorting,
  onSortingChange,
  rowSelection,
  onRowSelectionChange,
  onRowClick,
  getRowId,
  columnVisibility,
  onColumnVisibilityChange,
  emptyMessage = "No data found",
  rowHeight = 44,
  headerHeight = 40,
  enableSelection = false,
  // Additive (R3 P1): row class (active/focus ring), right-click per row,
  // a custom empty overlay node and the grid's accessible name.
  getRowClassName,
  onRowContextMenu,
  emptyContent,
  ariaLabel,
  // Additive (one view, ONE-VIEW-SPEC §3.2 / §3.10): `layout` from
  // useColumnLayout (order, hide, freeze, widths), `headerMenu` (the Excel
  // header menu: right-click, ⋮, Shift+F10), `selectOnHover` (checkboxes on
  // row hover / focus, or on every row once one is selected). Every
  // existing caller is unchanged.
  layout,
  headerMenu = false,
  selectOnHover = false,
  // Additive (W9 A3, ListFilter/useGroupedGrid.js): `groups` render as
  // full-width header rows in the same grid (not selectable; a row in two
  // groups shows in both); `collapsedGroups` / `onToggleGroup(key)`;
  // `group` / `onGroup` give `groupable` layout columns "Group by" in the
  // header menu; `groupNoun` names the rows. Without `groups`: unchanged.
  groups,
  collapsedGroups,
  onToggleGroup,
  group,
  onGroup,
  groupNoun,
}) {
  const theme = useTheme();
  const lay = layout ?? NO_LAYOUT;
  const rootRef = useRef(null);
  const layoutById = useMemo(
    () => new Map((lay.columns ?? []).map((c) => [c.id, c])),
    [lay.columns],
  );
  const menuLayout = useMemo(() => {
    if (!layout) return null;
    const enable = new Map(
      columns.map((c) => [c.id || c.accessorKey, c.enableSorting !== false]),
    );
    return {
      ...layout,
      columns: layout.columns.map((c) =>
        c.sortable === undefined && enable.has(c.id)
          ? { ...c, sortable: enable.get(c.id) }
          : c,
      ),
    };
  }, [layout, columns]);
  const hm = useHeaderMenu(
    menuLayout ?? { columns: [], canMoveBy: () => ({ ok: false }) },
    {
      sort: sorting?.[0]
        ? { id: sorting[0].id, desc: Boolean(sorting[0].desc) }
        : null,
      onSort: onSortingChange
        ? (next) => onSortingChange(next ? [next] : [])
        : undefined,
      group,
      onGroup,
    },
  );
  const menuOn = Boolean(headerMenu && layout);
  const gr = useGroupRows({
    groups,
    collapsedGroups,
    getRowId,
    getRowClassName,
  });

  // Convert our column format → MUI DataGrid columns
  const muiColumns = useMemo(() => {
    const list = columns.map((col) => {
      const field = col.id || col.accessorKey;
      const dataKey = col.accessorKey || col.id;
      const muiCol = {
        field,
        headerName: col.header,
        sortable: col.enableSorting !== false,
        disableColumnMenu: true,
      };
      // MIT DataGrid has no column resize (DL#42): no `resizable` prop.
      if (col.headerAlign) muiCol.headerAlign = col.headerAlign;
      if (col.align) muiCol.align = col.align;
      if (col.description) muiCol.description = col.description;
      if (dataKey !== field) {
        muiCol.valueGetter = (params) => params.row[dataKey];
      }

      // Opt-in custom header node. `headerName` stays a string (aria-label /
      // sort tooltip); a column that needs a rich header supplies renderHeader.
      if (col.renderHeader) {
        muiCol.renderHeader = col.renderHeader;
      }
      const shaped = layoutById.get(field);
      if (menuOn && shaped) {
        const inner = col.renderHeader;
        muiCol.renderHeader = (params) => (
          <Stack direction="row" alignItems="center" sx={{ minWidth: 0 }}>
            <Box
              component="span"
              sx={{
                minWidth: 0,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                fontSize: "13px",
                fontWeight: 500,
                color: "text.secondary",
              }}
            >
              {inner ? inner(params) : col.header}
            </Box>
            <HeaderMenuButton
              label={shaped.label}
              onOpen={(el) => hm.open(field, el)}
            />
          </Stack>
        );
      }

      // Width
      if (shaped?.width) {
        muiCol.width = shaped.width;
      } else if (col.meta?.flex) {
        muiCol.flex = col.meta.flex;
        if (col.minSize) muiCol.minWidth = col.minSize;
      } else if (col.size) {
        muiCol.width = col.size;
      } else {
        muiCol.flex = 1;
      }

      // Cell renderer
      if (col.cell) {
        const CellFn = col.cell;
        muiCol.renderCell = (params) =>
          CellFn({
            getValue: () => params.value,
            row: { original: params.row, getIsSelected: () => false },
            column: col,
            tabIndex: params.tabIndex,
          });
      }

      return muiCol;
    });
    if (!layout) return list;
    const order = reorderKnown(
      list.map((c) => c.field),
      (lay.columns ?? []).map((c) => c.id),
    );
    const byField = new Map(list.map((c) => [c.field, c]));
    return order.map((f) => byField.get(f));
    // hm.open is stable; the menu reads the latest layout itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns, layout, layoutById, menuOn]);

  // Sort model — convert our format to MUI format
  const sortModel = useMemo(() => {
    if (!sorting?.length) return [];
    return sorting.map((s) => ({
      field: s.id,
      sort: s.desc ? "desc" : "asc",
    }));
  }, [sorting]);

  const handleSortModelChange = useCallback(
    (newModel) => {
      if (!onSortingChange) return;
      if (newModel.length === 0) {
        onSortingChange([]);
      } else {
        const s = newModel[0];
        onSortingChange([{ id: s.field, desc: s.sort === "desc" }]);
      }
    },
    [onSortingChange],
  );

  // Row selection — convert between array (MUI) and object (our API)
  const selectionModel = useMemo(() => {
    if (!rowSelection) return [];
    const ids = Object.keys(rowSelection)
      .filter((k) => rowSelection[k])
      .map((k) => {
        const row = (data || [])[parseInt(k, 10)];
        return row ? (getRowId ? getRowId(row) : row.id) : k;
      })
      .filter(Boolean);
    return gr.toGridSelection(ids);
  }, [rowSelection, data, getRowId, gr]);

  const handleSelectionChange = useCallback(
    (newSelection) => {
      if (!onRowSelectionChange) return;
      const picked = gr.fromGridSelection(
        newSelection,
        (data || []).filter((_, i) => rowSelection?.[i]).map(gr.rowIdOf),
      );
      const obj = {};
      (data || []).forEach((row, idx) => {
        const key = getRowId ? getRowId(row) : row.id;
        if (picked.includes(key)) obj[idx] = true;
      });
      onRowSelectionChange(obj);
    },
    [onRowSelectionChange, data, getRowId, gr, rowSelection],
  );

  // A stable overlay component; its content follows `emptyContent`.
  const emptyRef = useRef(emptyContent);
  emptyRef.current = emptyContent;
  const slots = useMemo(
    () =>
      emptyContent
        ? {
            noRowsOverlay: function EmptyOverlay() {
              return <Box sx={{ py: 2 }}>{emptyRef.current}</Box>;
            },
          }
        : undefined,
    [Boolean(emptyContent)], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // Column visibility — convert our format to MUI format. With a layout, a
  // column is hidden when the layout OR the caller hides it.
  const muiColumnVisibility = useMemo(() => {
    const own = columnVisibility ?? {};
    if (!layout) return own;
    const fromLayout = toTanstackState(lay.columns).columnVisibility;
    const out = { ...fromLayout };
    Object.entries(own).forEach(([k, v]) => {
      if (v === false) out[k] = false;
    });
    return out;
  }, [columnVisibility, layout, lay.columns]);

  const gridColumns = useGroupColumns({
    grouped: gr.grouped,
    columns: muiColumns,
    visibility: muiColumnVisibility,
    groupNoun,
    onToggleGroup,
  });

  // Freeze (layout `pinned: "left"`): the MIT grid has no pinning, so the
  // frozen columns (always first) stick: body cells `position: sticky`,
  // header cells follow the horizontal scroll back. Offsets are measured
  // from the rendered header cells (flex widths included).
  const pinnedFields = useMemo(
    () =>
      layout
        ? (lay.columns ?? [])
            .filter((c) => c.pinned === "left" && !c.hidden)
            .map((c) => c.id)
        : [],
    [layout, lay.columns],
  );
  const [pinLefts, setPinLefts] = useState({});
  useEffect(() => {
    const root = rootRef.current;
    if (!root || !pinnedFields.length) {
      // No frozen columns: keep the same object (no extra render for the
      // callers that never pass a layout).
      setPinLefts((cur) => (Object.keys(cur).length ? {} : cur));
      return undefined;
    }
    const fields = [...(enableSelection ? ["__check__"] : []), ...pinnedFields];
    const measure = () => {
      let left = 0;
      const next = {};
      fields.forEach((f) => {
        const el = root.querySelector(
          `.MuiDataGrid-columnHeader[data-field="${CSS.escape(f)}"]`,
        );
        next[f] = left;
        left += el ? el.getBoundingClientRect().width : 0;
      });
      setPinLefts((cur) =>
        JSON.stringify(cur) === JSON.stringify(next) ? cur : next,
      );
    };
    const scroller = root.querySelector(".MuiDataGrid-virtualScroller");
    const onScroll = () =>
      root.style.setProperty(
        "--fa-scroll-left",
        `${scroller?.scrollLeft ?? 0}px`,
      );
    measure();
    onScroll();
    scroller?.addEventListener("scroll", onScroll, { passive: true });
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(measure)
        : null;
    ro?.observe(root);
    return () => {
      scroller?.removeEventListener("scroll", onScroll);
      ro?.disconnect();
    };
  }, [pinnedFields, enableSelection, muiColumns]);
  const pinSx = useMemo(() => {
    const out = {};
    const last = Object.keys(pinLefts).pop();
    Object.entries(pinLefts).forEach(([f, left]) => {
      const sel = `[data-field="${f}"]`;
      const edge =
        f === last ? { boxShadow: `1px 0 0 ${theme.palette.divider}` } : {};
      out[`& .MuiDataGrid-cell${sel}`] = {
        position: "sticky",
        left: `${left}px`,
        zIndex: 2,
        bgcolor: "background.paper",
        ...edge,
      };
      out[`& .MuiDataGrid-columnHeader${sel}`] = {
        transform: "translateX(var(--fa-scroll-left, 0px))",
        zIndex: 3,
        bgcolor: "background.paper",
        ...edge,
      };
    });
    return out;
  }, [pinLefts, theme.palette.divider]);

  const hasSelection = selectionModel.length > 0;
  const onRootContextMenu = menuOn
    ? (e) => {
        const head = e.target.closest?.(
          ".MuiDataGrid-columnHeader[data-field]",
        );
        const field = head?.getAttribute("data-field");
        if (!field || !layoutById.has(field)) return;
        e.preventDefault();
        hm.open(field, e);
      }
    : undefined;
  const onRootKeyDown = menuOn
    ? (e) => {
        if (!isMenuKey(e)) return;
        const head = e.target.closest?.(
          ".MuiDataGrid-columnHeader[data-field]",
        );
        const field = head?.getAttribute("data-field");
        if (!field || !layoutById.has(field)) return;
        e.preventDefault();
        e.stopPropagation();
        hm.open(field, head);
      }
    : undefined;

  return (
    <Box
      ref={rootRef}
      className={
        selectOnHover && enableSelection
          ? hoverSelectClass(hasSelection)
          : undefined
      }
      onContextMenu={onRootContextMenu}
      onKeyDownCapture={onRootKeyDown}
      sx={{ flex: 1, minWidth: 0, minHeight: 0, height: "100%" }}
    >
      <DataGrid
        rows={gr.rows ?? (data || [])}
        columns={gridColumns}
        loading={isLoading}
        getRowId={gr.getGridRowId ?? (getRowId || ((row) => row.id))}
        isRowSelectable={gr.isRowSelectable}
        rowCount={rowCount}
        rowHeight={rowHeight}
        columnHeaderHeight={headerHeight}
        // Server-side — we only pass the current page of data,
        // so tell DataGrid not to paginate internally
        paginationMode="server"
        sortingMode="server"
        filterMode="server"
        // Sort
        sortModel={sortModel}
        onSortModelChange={handleSortModelChange}
        // Selection
        checkboxSelection={enableSelection}
        rowSelectionModel={selectionModel}
        onRowSelectionModelChange={handleSelectionChange}
        disableRowSelectionOnClick
        // Column visibility
        columnVisibilityModel={muiColumnVisibility}
        onColumnVisibilityModelChange={onColumnVisibilityChange}
        // Row click
        onRowClick={
          onRowClick
            ? (params) => !params.row.__group && onRowClick(params.row)
            : undefined
        }
        // No virtualization — paginated at 50 rows max, plain DOM = no white flash
        disableVirtualization
        // Hide built-in pagination/footer — we use our own
        hideFooter
        // Disable built-in toolbar
        disableColumnFilter
        disableColumnSelector
        disableDensitySelector
        // Styling
        localeText={{ noRowsLabel: emptyMessage }}
        getRowClassName={gr.getRowClassName}
        aria-label={ariaLabel}
        slots={slots}
        slotProps={
          onRowContextMenu
            ? {
                row: {
                  onContextMenu: (event) => {
                    const id = event.currentTarget?.getAttribute("data-id");
                    const row = gr.findRow
                      ? gr.findRow(id)
                      : (data || []).find(
                          (r) => String(getRowId ? getRowId(r) : r.id) === id,
                        );
                    if (!row) return;
                    event.preventDefault();
                    onRowContextMenu(row, event);
                  },
                },
              }
            : undefined
        }
        sx={{
          border: "none",
          bgcolor: "background.paper",
          // Header
          "& .MuiDataGrid-columnHeaders": {
            bgcolor: "background.paper",
            borderBottom: "1px solid",
            borderColor: "divider",
            minHeight: `${headerHeight}px !important`,
            maxHeight: `${headerHeight}px !important`,
          },
          "--DataGrid-containerBackground": theme.palette.background.paper,
          "& .MuiDataGrid-columnHeaderTitle": {
            fontSize: "13px",
            fontWeight: 500,
            color: theme.palette.text.secondary,
          },
          "& .MuiDataGrid-iconButtonContainer": {
            color: theme.palette.text.secondary,
          },
          // Cells
          "& .MuiDataGrid-cell": {
            borderBottom: "1px solid",
            borderColor: "divider",
            fontSize: "13px",
            cursor: onRowClick ? "pointer" : "default",
            // Keep pointer focus quiet; delegated controls own their ring.
            "&:not(:focus-visible)": { outline: "none" },
            "&:focus-visible": {
              outline: `2px solid ${theme.palette.border?.focus ?? theme.palette.primary.main}`,
              outlineOffset: "-2px",
            },
          },
          // Rows
          "& .MuiDataGrid-row:hover": {
            bgcolor:
              theme.palette.mode === "dark"
                ? "rgba(255,255,255,0.03)"
                : "rgba(0,0,0,0.02)",
          },
          // Keyboard focus ring (J/K roving row) and selection tint.
          "& .MuiDataGrid-row.row-active": {
            outline: `2px solid ${theme.palette.primary.main}`,
            outlineOffset: "-2px",
          },
          "& .MuiDataGrid-row.Mui-selected, & .MuiDataGrid-row.Mui-selected:hover":
            {
              bgcolor: alpha(
                theme.palette.primary.main,
                theme.palette.mode === "dark" ? 0.16 : 0.08,
              ),
            },
          // Sort arrow only on hover or when sorted; the active one is primary.
          "& .MuiDataGrid-columnHeader--sorted .MuiDataGrid-iconButtonContainer":
            { color: theme.palette.primary.main },
          // Clean up
          "& .MuiDataGrid-columnSeparator": { display: "none" },
          "& .MuiDataGrid-columnHeader:not(:focus-visible)": {
            outline: "none",
          },
          "& .MuiDataGrid-columnHeader:focus-visible": {
            outline: `2px solid ${theme.palette.border?.focus ?? theme.palette.primary.main}`,
            outlineOffset: "-2px",
          },
          "& .MuiDataGrid-overlayWrapper": { minHeight: 200 },
          // W9 A3: group header rows: a quiet band, no checkbox.
          "& .MuiDataGrid-row.fa-group-row": {
            bgcolor: alpha(theme.palette.text.primary, 0.03),
            cursor: "default",
          },
          "& .MuiDataGrid-row.fa-group-row .MuiDataGrid-cellCheckbox > *": {
            visibility: "hidden",
          },
          ...pinSx,
        }}
      />
      {menuOn && <HeaderMenu {...hm.menuProps} />}
    </Box>
  );
}
