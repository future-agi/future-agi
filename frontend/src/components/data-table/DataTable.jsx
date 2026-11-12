/* eslint-disable react/prop-types */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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
// The always-visible bars `showScrollbar` draws: the app's 8 px bars. Column
// budgets count the vertical one out of the table's width. DataGrid is told
// the size (its `scrollbarSize`): it measures a probe div of its own, which
// gets the app's overlay bars (0 px on macOS), and would lay the columns out
// 8 px too wide for a scroller that draws these bars.
const SCROLLBAR_SIZE = 8;

/** `ids` reordered: the known ids take the slots known ids held, in
 * `order`; unknown ids keep their place. Pure. */
function reorderKnown(ids, order) {
  const rank = new Map(order.map((id, i) => [id, i]));
  const known = ids.filter((id) => rank.has(id));
  known.sort((a, b) => rank.get(a) - rank.get(b));
  let k = 0;
  return ids.map((id) => (rank.has(id) ? known[k++] : id));
}
import EmptyContent from "src/components/empty-content";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";

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
  // Query identity: page, size, raw/debounced search, filters and route/tab.
  selectionScope,
  // Non-destructive pickers may own an external cross-page selection.
  preserveSelectionOnUnmount = false,
  onRowClick,
  getRowId,
  columnVisibility,
  onColumnVisibilityChange,
  emptyMessage = "No data found",
  rowHeight = ROW_HEIGHT.default,
  headerHeight = TABLE_HEADER_HEIGHT.default,
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
  activeRowId,
  // Additive (wide tables), both off by default: `showScrollbar` draws
  // the grid's own scrollbars as always-visible 8 px bars, so columns past
  // the right edge read as reachable (macOS shows overlay bars only while
  // you scroll); `pinnedRight` names one column, the row actions, that
  // stays on the right edge while the table scrolls sideways. Either one
  // also scrolls a cell or control that takes focus into view.
  showScrollbar = false,
  pinnedRight,
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

  useEffect(() => {
    if (activeRowId == null) return;
    rootRef.current
      ?.querySelector(".DataTable-activeRow")
      ?.scrollIntoView?.({ block: "nearest" });
  }, [activeRowId, data, groups, collapsedGroups]);

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
        // A right-aligned (numeric) header puts its ⋮ before the label, so
        // the label ends where the column's values end.
        const lead = col.headerAlign === "right";
        muiCol.renderHeader = (params) => {
          const menuButton = (
            <HeaderMenuButton
              label={shaped.label}
              onOpen={(el) => hm.open(field, el)}
              sx={lead ? { ml: 0, mr: 0.25 } : undefined}
            />
          );
          return (
            <Stack direction="row" alignItems="center" sx={{ minWidth: 0 }}>
              {lead && menuButton}
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
              {!lead && menuButton}
            </Stack>
          );
        };
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

  // Controlled contract: { [recordId]: true }, never page-local indices.
  // The second callback argument contains only selected records on this page.
  const scopeKey = JSON.stringify([selectionScope, sorting]);
  const selectionSync = useRef({ scopeKey, ids: null, rows: null });
  const selectionCallback = useRef(null);
  const scopeChanged = selectionSync.current.scopeKey !== scopeKey;
  const selectedRows = useMemo(
    () =>
      scopeChanged
        ? []
        : (data || []).filter((row) => {
            const id = getRowId ? getRowId(row) : row.id;
            return (
              Object.prototype.hasOwnProperty.call(rowSelection || {}, id) &&
              Boolean(rowSelection[id])
            );
          }),
    [data, getRowId, rowSelection, scopeChanged],
  );
  const selectedRecordIds = selectedRows.map(gr.rowIdOf);
  const selectionModel = gr.toGridSelection(selectedRecordIds);
  const selectedKey = JSON.stringify(
    Object.keys(rowSelection || {})
      .filter((id) => rowSelection[id])
      .sort(),
  );
  const modelKey = JSON.stringify(selectedRecordIds.map(String).sort());
  const rowsKey = JSON.stringify(
    (data || []).map((row) => (getRowId ? getRowId(row) : row.id)),
  );

  // Reconcile before the user can act on a changed view. Identity-equivalent
  // refetches do not clear selection; removed IDs are dropped from the owner as
  // well as the checkboxes, so they cannot reappear selected on a later poll.
  useLayoutEffect(() => {
    selectionCallback.current = enableSelection ? onRowSelectionChange : null;
    const previous = selectionSync.current;
    const notify =
      previous.scopeKey !== scopeKey ||
      selectedKey !== modelKey ||
      previous.ids !== modelKey ||
      previous.rows !== rowsKey;
    selectionSync.current = { scopeKey, ids: modelKey, rows: rowsKey };
    if (notify && enableSelection) {
      onRowSelectionChange?.(
        Object.fromEntries(selectedRecordIds.map((id) => [id, true])),
        selectedRows,
      );
    }
  });

  // Also clear callback-owned snapshots when a parent hides this table (empty
  // state, tab switch, route change), rather than leaving an actionable toolbar.
  useEffect(
    () => () => {
      if (!preserveSelectionOnUnmount) selectionCallback.current?.({}, []);
    },
    [preserveSelectionOnUnmount],
  );

  const handleSelectionChange = useCallback(
    (newSelection) => {
      if (!onRowSelectionChange) return;
      const ids = new Set(
        gr.fromGridSelection(
          newSelection,
          scopeChanged ? [] : selectedRecordIds,
        ),
      );
      const records = scopeChanged
        ? []
        : (data || []).filter((row) =>
            ids.has(getRowId ? getRowId(row) : row.id),
          );
      onRowSelectionChange(
        Object.fromEntries(
          records.map((row) => [getRowId ? getRowId(row) : row.id, true]),
        ),
        records,
      );
    },
    [onRowSelectionChange, data, getRowId, scopeChanged, gr, selectedRecordIds],
  );

  // A stable overlay component; its content follows `emptyContent`.
  const emptyRef = useRef({ emptyContent, emptyMessage });
  emptyRef.current = { emptyContent, emptyMessage };
  const slots = useMemo(
    () => ({
      noRowsOverlay: function EmptyOverlay() {
        const current = emptyRef.current;
        return current.emptyContent != null ? (
          <Box sx={{ py: 2 }}>{current.emptyContent}</Box>
        ) : (
          <EmptyContent title={current.emptyMessage} sx={{ height: 1 }} />
        );
      },
    }),
    [],
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
  // `pinnedRight` counts only when the table has that column.
  const pinRight = useMemo(() => {
    const col = pinnedRight
      ? columns.find((c) => (c.id || c.accessorKey) === pinnedRight)
      : null;
    return col ? { field: pinnedRight, width: col.size ?? 0 } : null;
  }, [columns, pinnedRight]);
  const pinRightField = pinRight?.field ?? null;
  const [pinLefts, setPinLefts] = useState({});
  // Frozen columns matter only while the table is wider than its box: then
  // they stick and end at a line. A table that fits never scrolls sideways,
  // so it gets no sticky cells and no line (that read as a stray divider).
  const [scrollsX, setScrollsX] = useState(false);
  // DataGrid mounts its scroller only after it has measured itself, which is
  // after this effect first runs; the effect runs again once it is there.
  const [scrollerMounts, setScrollerMounts] = useState(0);
  useEffect(() => {
    const root = rootRef.current;
    if (!root || (!pinnedFields.length && !pinRightField)) {
      // No frozen columns: keep the same object (no extra render for the
      // callers that never pass a layout).
      setPinLefts((cur) => (Object.keys(cur).length ? {} : cur));
      setScrollsX(false);
      return undefined;
    }
    // With only a right pin, the checkbox column does not stick either.
    const fields = pinnedFields.length
      ? [...(enableSelection ? ["__check__"] : []), ...pinnedFields]
      : [];
    const scroller = root.querySelector(".MuiDataGrid-virtualScroller");
    const waitForScroller =
      !scroller && typeof MutationObserver !== "undefined"
        ? new MutationObserver(() => {
            if (!root.querySelector(".MuiDataGrid-virtualScroller")) return;
            waitForScroller.disconnect();
            setScrollerMounts((n) => n + 1);
          })
        : null;
    waitForScroller?.observe(root, { childList: true, subtree: true });
    // Header cells sit outside the scroller and follow its sideways scroll
    // back: a left pin by scrollLeft, a right pin by scrollLeft less the
    // furthest it can scroll (0 at the right end).
    const onScroll = () => {
      const left = scroller?.scrollLeft ?? 0;
      const max = scroller ? scroller.scrollWidth - scroller.clientWidth : 0;
      root.style.setProperty("--fa-scroll-left", `${left}px`);
      root.style.setProperty(
        "--fa-scroll-right",
        `${Math.min(0, left - Math.max(0, max))}px`,
      );
    };
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
      const box = root.querySelector(".MuiDataGrid-virtualScroller");
      setScrollsX(Boolean(box) && box.scrollWidth - box.clientWidth > 1);
      // A resize changes how far the table can scroll.
      onScroll();
    };
    measure();
    scroller?.addEventListener("scroll", onScroll, { passive: true });
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(measure)
        : null;
    ro?.observe(root);
    // Columns and scrollbars change the scroller and its content, not root.
    if (scroller) ro?.observe(scroller);
    const content = scroller?.querySelector(
      ".MuiDataGrid-virtualScrollerContent",
    );
    if (content) ro?.observe(content);
    return () => {
      scroller?.removeEventListener("scroll", onScroll);
      ro?.disconnect();
      waitForScroller?.disconnect();
    };
  }, [
    pinnedFields,
    enableSelection,
    muiColumns,
    pinRightField,
    scrollerMounts,
  ]);
  const pinSx = useMemo(() => {
    const out = {};
    if (!scrollsX) return out;
    if (pinRight) {
      // The pinned cell is opaque, so it repeats the row's hover, selection
      // and active tints over its paper fill.
      const sel = `[data-field="${pinRight.field}"]`;
      const tint = (color) => ({
        backgroundImage: `linear-gradient(${color}, ${color})`,
      });
      const edge = { boxShadow: `-1px 0 0 ${theme.palette.divider}` };
      out[`& .MuiDataGrid-cell${sel}`] = {
        position: "sticky",
        right: 0,
        zIndex: 2,
        bgcolor: "background.paper",
        ...edge,
      };
      out[`& .MuiDataGrid-row:hover .MuiDataGrid-cell${sel}`] = tint(
        theme.palette.action.hover,
      );
      out[`& .MuiDataGrid-row.Mui-selected .MuiDataGrid-cell${sel}`] = tint(
        alpha(
          theme.palette.primary.main,
          theme.palette.mode === "dark" ? 0.16 : 0.08,
        ),
      );
      out[`& .MuiDataGrid-row.DataTable-activeRow .MuiDataGrid-cell${sel}`] =
        tint(theme.palette.action.selected);
      out[`& .MuiDataGrid-columnHeader${sel}`] = {
        transform: "translateX(var(--fa-scroll-right, 0px))",
        zIndex: 3,
        bgcolor: "background.paper",
        ...edge,
      };
    }
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
  }, [pinLefts, pinRight, scrollsX, theme]);

  // The grid's scroller: always-visible bars on request, and room on the
  // right for the pinned column, so a cell scrolled into view is not left
  // under it.
  const scrollerSx = useMemo(() => {
    const out = {};
    if (showScrollbar) {
      const thumb = alpha(theme.palette.text.primary, 0.32);
      Object.assign(out, {
        // The app's `* { scrollbar-width: thin; scrollbar-color }` makes
        // Chromium ignore ::-webkit-scrollbar and keep overlay bars, which
        // macOS hides until you scroll. Reset, the bars below are drawn.
        scrollbarWidth: "auto",
        scrollbarColor: "auto",
        "&::-webkit-scrollbar": {
          width: `${SCROLLBAR_SIZE}px`,
          height: `${SCROLLBAR_SIZE}px`,
        },
        "&::-webkit-scrollbar-track": {
          backgroundColor: alpha(theme.palette.text.primary, 0.04),
        },
        "&::-webkit-scrollbar-thumb": {
          backgroundColor: thumb,
          borderRadius: `${SCROLLBAR_SIZE / 2}px`,
          border: "1px solid transparent",
          backgroundClip: "content-box",
        },
        "&::-webkit-scrollbar-thumb:hover": {
          backgroundColor: alpha(theme.palette.text.primary, 0.48),
        },
        "&::-webkit-scrollbar-corner": { backgroundColor: "transparent" },
        // Firefox has no ::-webkit-scrollbar: thin bars in the same colours.
        "@supports (-moz-appearance: none)": {
          scrollbarWidth: "thin",
          scrollbarColor: `${thumb} transparent`,
        },
      });
    }
    if (scrollsX && pinRight?.width) {
      out.scrollPaddingRight = `${pinRight.width}px`;
    }
    return out;
  }, [showScrollbar, scrollsX, pinRight, theme]);

  // A cell or control that takes focus (Tab, arrow keys) is scrolled fully
  // into view, clear of the pinned column. The pinned cell sticks to the
  // right edge, so it is always in view: focusing or clicking a control in
  // it scrolls nothing. Its box sits inside the scroller's right padding,
  // which made "into view" slide the table sideways; that padding is off
  // while the pinned cell holds focus, so the browser's own focus scroll
  // (HTML's focusing steps fire the focus event first, then scroll) leaves
  // the table where it is too.
  const onRootFocus =
    showScrollbar || pinRight
      ? (e) => {
          const scroller = e.target?.closest?.(".MuiDataGrid-virtualScroller");
          if (!scroller) return;
          const cell = e.target.closest(".MuiDataGrid-cell");
          const inPin =
            Boolean(pinRight) &&
            cell?.getAttribute("data-field") === pinRight.field;
          scroller.style.scrollPaddingRight = inPin ? "0px" : "";
          if (inPin) return;
          e.target.scrollIntoView?.({ block: "nearest", inline: "nearest" });
        }
      : undefined;

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
      onFocus={onRootFocus}
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
        scrollbarSize={showScrollbar ? SCROLLBAR_SIZE : undefined}
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
        getRowClassName={(params) =>
          [
            gr.getRowClassName?.(params),
            !params.row.__group &&
            activeRowId != null &&
            String(getRowId ? getRowId(params.row) : params.row.id) ===
              String(activeRowId)
              ? "DataTable-activeRow"
              : "",
          ]
            .filter(Boolean)
            .join(" ")
        }
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
          // Design system table: headers 11/500 in fg.tertiary, values 12/18.
          "& .MuiDataGrid-columnHeaderTitle": {
            fontSize: "11px",
            lineHeight: "16px",
            fontWeight: 500,
            color: theme.palette.text.subtitle,
          },
          "& .MuiDataGrid-iconButtonContainer": {
            color: theme.palette.text.secondary,
          },
          // Cells
          "& .MuiDataGrid-cell": {
            borderBottom: "1px solid",
            borderColor: "divider",
            fontSize: "12px",
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
            bgcolor: "action.hover",
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
          "& .MuiDataGrid-row.DataTable-activeRow, & .MuiDataGrid-row.DataTable-activeRow:hover":
            { bgcolor: "action.selected" },
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
          ...(Object.keys(scrollerSx).length
            ? { "& .MuiDataGrid-virtualScroller": scrollerSx }
            : {}),
        }}
      />
      {menuOn && <HeaderMenu {...hm.menuProps} />}
    </Box>
  );
}
