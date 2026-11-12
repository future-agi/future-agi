import React, {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import CustomExperimentGroupHeader from "../ExperimentData/CustomExperimentGroupHeader";
import {
  ResultPreferenceColumnHeader,
  ResultRunColumnHeader,
} from "../ExperimentData/CustomExperimentColumnHeader";
import EmptyState from "src/sections/common/ux/EmptyState";
import ResultMetricHeader from "./headers/ResultMetricHeader";
import useHoverSelection from "src/sections/common/ux/oneview/useHoverSelection";
import { isUserColumnEvent } from "src/sections/common/ux/oneview/useAgColumnShaping";
import {
  headerMenuItems,
  toAgMenuItems,
} from "src/sections/common/ux/oneview/headerMenuItems";
import { isMenuKey } from "src/sections/common/ux/oneview/HeaderMenu";
import { keyOfColId } from "./resultsColumns";
import {
  suppressGridTyping,
  useRowCursor,
} from "src/sections/common/ux/gridKeys";
import {
  ExpectedCell,
  InputCell,
  MetaCell,
  RowCell,
  SplitsCell,
  WhyCell,
} from "./cells/ExampleCells";
import { ProductionCell } from "./cells/ProductionCell";
import OutputCell from "./cells/OutputCell";
import {
  OpsCell,
  PreferenceCell,
  ScoreCellRenderer,
  StepsCell,
} from "./cells/MetricCells";

/**
 * Empty grid with a next step (RC#43): `No rows match "zzzz"` · Clear
 * search, or `No regressed rows vs #2B. Nice.` · Show all rows · Change
 * baseline.
 */
function NoRowsOverlay({ context }) {
  const e = context?.empty ?? {};
  return (
    <Box sx={{ pointerEvents: "auto", maxWidth: 520 }}>
      <EmptyState
        compact
        icon="mdi:filter-remove-outline"
        title={e.title ?? "No rows match these filters"}
        body={e.body}
        actions={e.actions}
      />
    </Box>
  );
}
NoRowsOverlay.propTypes = { context: PropTypes.object };

const COMPONENTS = {
  rowCell: RowCell,
  splitsCell: SplitsCell,
  inputCell: InputCell,
  expectedCell: ExpectedCell,
  metaCell: MetaCell,
  outputCell: OutputCell,
  scoreCell: ScoreCellRenderer,
  opsCell: OpsCell,
  stepsCell: StepsCell,
  preferenceCell: PreferenceCell,
  whyCell: WhyCell,
  productionCell: ProductionCell,
  noRowsOverlay: NoRowsOverlay,
  resultGroupHeader: CustomExperimentGroupHeader,
  resultRunHeader: ResultRunColumnHeader,
  resultMetricHeader: ResultMetricHeader,
  resultPreferenceHeader: ResultPreferenceColumnHeader,
};

const DEFAULT_COL_DEF = {
  sortable: false,
  filter: false,
  resizable: true,
  suppressHeaderMenuButton: true,
  cellStyle: { padding: 0 },
  // 8f, D25: printable keys are shortcuts (J / K / X / A / C /
  // ?), never typing into a cell.
  suppressKeyboardEvent: suppressGridTyping,
};

const SELECTION_COLUMN_DEF = {
  pinned: "left",
  lockPinned: true,
  width: 44,
  minWidth: 44,
  maxWidth: 44,
  resizable: false,
};

/** Run id of a results column id (`<metric>|<runId>`), else null. */
export const runOfColumn = (colId) => {
  const parts = String(colId ?? "").split("|");
  return parts.length === 2 && parts[1] !== "human" && parts[1] !== "judge"
    ? parts[1]
    : null;
};

/** The new key order after a user drag (pure; exported for tests). */
export function movedIndex(layoutIds, displayedKeys, key) {
  const order = [...new Set(displayedKeys)];
  const next = order[order.indexOf(key) + 1];
  const ids = layoutIds.filter((x) => x !== key);
  const at = next ? ids.indexOf(next) : -1;
  return at < 0 ? ids.length : at;
}

/**
 * The results AG Grid (client-side row model: R3 returns every row in
 * view). Server-side filters and sort; the grid only renders. Row click
 * opens the drawer focused on the clicked run's column.
 *
 * One view (A3-11): `layout` (useColumnLayout over resultsColumnCatalog)
 * shapes the columns (the defs arrive shaped); what the user does in the
 * grid (drag a column, drag-freeze, resize) is written back to it, and AG
 * Grid's own column menu (header ⋮, right-click, Shift+F10) is the Excel
 * header menu for the column's catalog entry (a per-run group = one entry).
 */
const ResultsGrid = forwardRef(function ResultsGrid(
  {
    rows,
    columnDefs,
    context,
    rowHeight,
    activeRowId,
    onOpenRow,
    onSelectionChanged,
    loading,
    layout,
    onCursorRow,
  },
  ref,
) {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.dataGrid);
  const localRef = useRef(null);
  const gridRef = ref ?? localRef;
  const hoverSelect = useHoverSelection(gridRef);
  // 8f: the first row has a cursor on load so J / K / Enter /
  // X work without a click; it comes back after `?`, a dialog or a toast.
  useRowCursor(gridRef, { surface: "results" });
  const live = useRef(layout);
  live.current = layout;
  const known = (key) =>
    Boolean(live.current?.catalog?.some((c) => c.id === key));

  const getMainMenuItems = useCallback((params) => {
    // A column, or a column group (right-click on a group's padding).
    const key = keyOfColId(
      params.column?.getColId?.() ?? params.columnGroup?.getGroupId?.(),
    );
    if (!live.current || !known(key)) return params.defaultItems;
    return toAgMenuItems(headerMenuItems(live.current, key));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const onColumnMoved = useCallback((e) => {
    const l = live.current;
    if (!l || !isUserColumnEvent(e) || !e.column) return;
    const key = keyOfColId(e.column.getColId());
    if (!known(key)) return;
    const shown = e.api
      .getColumnState()
      .map((c) => keyOfColId(c.colId))
      .filter(known);
    l.move(
      key,
      movedIndex(
        l.columns.map((c) => c.id),
        shown,
        key,
      ),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const onColumnPinned = useCallback((e) => {
    const l = live.current;
    if (!l || !isUserColumnEvent(e)) return;
    const keys = [
      ...new Set(
        (e.columns ?? (e.column ? [e.column] : [])).map((c) =>
          keyOfColId(c.getColId()),
        ),
      ),
    ].filter(known);
    keys.forEach((k) => l.pin(k, e.pinned === "left" ? "left" : null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const onColumnResized = useCallback((e) => {
    const l = live.current;
    if (!l || !isUserColumnEvent(e)) return;
    const cols = e.columns ?? (e.column ? [e.column] : []);
    const seen = new Set();
    cols.forEach((c) => {
      const key = keyOfColId(c.getColId());
      if (!known(key) || seen.has(key)) return;
      seen.add(key);
      l.setWidth(key, c.getActualWidth());
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // IT-R5 G3 (P1): run columns past the right edge get a fade and a cue
  // (`3 more runs →`) that scrolls the next one into view.
  const [offRight, setOffRight] = useState(null);
  const measureOff = useCallback((e) => {
    const api = e?.api ?? gridRef.current?.api;
    if (!api || api.isDestroyed?.()) return;
    const range = api.getHorizontalPixelRange?.();
    if (!range) return;
    const runs = new Map();
    (api.getAllDisplayedColumns?.() ?? []).forEach((col) => {
      if (col.getPinned?.()) return;
      const runId = String(col.getColId?.() ?? "").split("|")[1];
      if (!runId) return;
      const left = col.getLeft?.();
      if (!Number.isFinite(left)) return;
      const visible = left + 24 < range.right;
      const cur = runs.get(runId);
      runs.set(runId, {
        visible: Boolean(cur?.visible) || visible,
        colId: cur?.colId ?? col.getColId(),
        left: Math.min(cur?.left ?? Infinity, left),
      });
    });
    const hidden = [...runs.values()]
      .filter((r) => !r.visible)
      .sort((a, b) => a.left - b.left);
    setOffRight((prev) => {
      const next = hidden.length
        ? { count: hidden.length, colId: hidden[0].colId }
        : null;
      return prev?.count === next?.count && prev?.colId === next?.colId
        ? prev
        : next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Shift+F10 / ContextMenu key on a focused header of this grid.
  useEffect(() => {
    const onKey = (e) => {
      if (!isMenuKey(e)) return;
      const api = gridRef.current?.api;
      const cell = document.activeElement?.closest?.(
        ".ag-header-cell[col-id], .ag-header-group-cell[col-id]",
      );
      if (!api || !cell || !cell.closest("[data-results-grid-root]")) return;
      const key = keyOfColId(cell.getAttribute("col-id"));
      if (!known(key)) return;
      const leaf = (api.getColumns?.() ?? []).find(
        (c) => keyOfColId(c.getColId()) === key,
      );
      if (!leaf) return;
      e.preventDefault();
      e.stopPropagation();
      api.showColumnMenu(leaf.getColId());
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridRef]);

  // Cells read `context`; refresh them when it changes (diff, reasons, …).
  useEffect(() => {
    gridRef.current?.api?.refreshCells({ force: true });
  }, [context, gridRef]);

  useEffect(() => {
    const api = gridRef.current?.api;
    if (!api) return;
    api.resetRowHeights();
  }, [rowHeight, gridRef]);

  useEffect(() => {
    const api = gridRef.current?.api;
    if (!api) return;
    if (loading) api.setGridOption("loading", true);
    else api.setGridOption("loading", false);
  }, [loading, gridRef]);

  const onCellClicked = useCallback(
    (e) => {
      if (e.column?.getColId?.() === "ag-Grid-SelectionColumn") return;
      const target = e.event?.target;
      if (target?.closest?.("button, a, [role='button'], .MuiChip-root"))
        return;
      onOpenRow?.(e.data, runOfColumn(e.column?.getColId?.()));
    },
    [onOpenRow],
  );

  // iterate r1 G3 (P5): one row cursor. The open row is styled by its
  // `row-id` (no redraw, never stale); the grid scrolls to it and, while
  // the grid has focus, its focused cell moves with it. Arrow keys in the
  // grid move the open row (`onCursorRow`); closing the panel leaves the
  // keyboard cursor on the row that was open.
  const rootRef = useRef(null);
  const navKeyAt = useRef(0);
  const prevActive = useRef(activeRowId);
  useEffect(() => {
    const api = gridRef.current?.api;
    const prev = prevActive.current;
    prevActive.current = activeRowId;
    if (!api || api.isDestroyed?.()) return;
    const id = activeRowId ?? prev;
    const node = id ? api.getRowNode?.(id) : null;
    if (!node || node.rowIndex == null) return;
    api.ensureNodeVisible?.(node);
    const inGrid = rootRef.current?.contains(document.activeElement);
    const closing = !activeRowId && Boolean(prev);
    if (!inGrid && !closing) return;
    const column =
      api.getFocusedCell?.()?.column ?? api.getAllDisplayedColumns?.()?.[1];
    if (column) api.setFocusedCell?.(node.rowIndex, column);
  }, [activeRowId, gridRef]);
  const onCellFocused = useCallback(
    (e) => {
      if (!activeRowId || !onCursorRow || e.rowIndex == null) return;
      if (Date.now() - navKeyAt.current > 600) return;
      const data = e.api.getDisplayedRowAtIndex(e.rowIndex)?.data;
      if (data && data.example_id !== activeRowId) onCursorRow(data);
    },
    [activeRowId, onCursorRow],
  );
  const activeSel = activeRowId
    ? `& .ag-row[row-id="${String(activeRowId).replace(/"/g, "")}"]`
    : null;

  return (
    <Box
      ref={rootRef}
      data-results-grid-root=""
      className={hoverSelect.className}
      onKeyDownCapture={(e) => {
        if (
          [
            "ArrowUp",
            "ArrowDown",
            "PageUp",
            "PageDown",
            "Home",
            "End",
          ].includes(e.key)
        )
          navKeyAt.current = Date.now();
      }}
      sx={{
        flex: 1,
        minHeight: 0,
        height: "100%",
        position: "relative",
        // A3-04 Compact: the steps chip and trial pager on hover / focus.
        "& .fa-compact-hover": { display: "none" },
        "& .ag-row-hover .fa-compact-hover, & .ag-row:focus-within .fa-compact-hover":
          { display: "inline-flex" },
        // The keyboard row cursor (panel closed): tint + a 2 px ring; the
        // focused cell keeps no box (the row is the unit). With the panel
        // open the open row is the cursor (tint + left bar).
        ...(activeSel
          ? {
              // iterate r3 G3 (P1): the open row is marked on every part
              // (a ring above heat-tinted cells + the left bar), so the row
              // the drawer shows is findable next to it.
              [activeSel]: {
                bgcolor: (t) => `${t.palette.primary.main}1A !important`,
                boxShadow: (t) => `inset 3px 0 0 ${t.palette.primary.main}`,
              },
              [`${activeSel}::after`]: {
                content: '""',
                position: "absolute",
                inset: 0,
                pointerEvents: "none",
                zIndex: 2,
                borderTop: (t) => `2px solid ${t.palette.primary.main}`,
                borderBottom: (t) => `2px solid ${t.palette.primary.main}`,
              },
              [`& .ag-pinned-left-cols-container ${activeSel.slice(2)}::after`]:
                {
                  borderLeft: (t) => `3px solid ${t.palette.primary.main}`,
                },
            }
          : {
              // A ring drawn above the cells (heat-tinted score cells
              // would cover an inset shadow): top + bottom on every part,
              // the left edge on the pinned part.
              "& .ag-row-focus": {
                bgcolor: (t) => `${t.palette.primary.main}14`,
              },
              "& .ag-row-focus::after": {
                content: '""',
                position: "absolute",
                inset: 0,
                pointerEvents: "none",
                zIndex: 2,
                borderTop: (t) => `2px solid ${t.palette.primary.main}`,
                borderBottom: (t) => `2px solid ${t.palette.primary.main}`,
              },
              "& .ag-pinned-left-cols-container .ag-row-focus::after": {
                borderLeft: (t) => `2px solid ${t.palette.primary.main}`,
              },
            }),
        "& .ag-cell-focus:not(.ag-cell-range-selected):focus-within, & .ag-cell-focus:not(.ag-cell-range-selected)":
          { border: "none !important", outline: "none" },
        "& .ag-header-group-cell": { px: 1.25 },
        "& .ag-header-cell": { px: 1.25 },
      }}
    >
      <AgGridReact
        ref={gridRef}
        theme={agTheme}
        rowData={rows}
        columnDefs={columnDefs}
        defaultColDef={DEFAULT_COL_DEF}
        components={COMPONENTS}
        context={context}
        rowHeight={rowHeight}
        headerHeight={64}
        groupHeaderHeight={40}
        getRowId={({ data }) => data.example_id}
        rowSelection={{
          mode: "multiRow",
          checkboxes: true,
          headerCheckbox: true,
          enableClickSelection: false,
        }}
        selectionColumnDef={SELECTION_COLUMN_DEF}
        onSelectionChanged={(e) => {
          hoverSelect.onSelectionChanged(e);
          onSelectionChanged?.(e.api.getSelectedRows());
        }}
        getMainMenuItems={getMainMenuItems}
        onColumnMoved={onColumnMoved}
        onColumnPinned={onColumnPinned}
        onColumnResized={(e) => {
          onColumnResized(e);
          measureOff(e);
        }}
        onBodyScroll={measureOff}
        onDisplayedColumnsChanged={measureOff}
        onGridSizeChanged={measureOff}
        onFirstDataRendered={measureOff}
        suppressDragLeaveHidesColumns
        suppressMenuHide={false}
        onCellClicked={onCellClicked}
        onCellFocused={onCellFocused}
        suppressColumnMoveAnimation
        animateRows={false}
        noRowsOverlayComponent="noRowsOverlay"
      />
      {offRight ? (
        <Box
          aria-hidden={false}
          data-testid="results-more-runs"
          sx={{
            position: "absolute",
            top: 0,
            right: 0,
            bottom: 0,
            width: 56,
            pointerEvents: "none",
            background: (t) =>
              `linear-gradient(to right, transparent, ${t.palette.background.paper})`,
            zIndex: 3,
          }}
        >
          <ButtonBase
            onClick={() => {
              const api = gridRef.current?.api;
              api?.ensureColumnVisible?.(offRight.colId, "start");
            }}
            sx={{
              pointerEvents: "auto",
              position: "absolute",
              top: 10,
              right: 6,
              height: 22,
              px: 0.75,
              borderRadius: 11,
              bgcolor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              boxShadow: 1,
              typography: "s3",
              fontWeight: 500,
              color: "text.secondary",
              whiteSpace: "nowrap",
              "&:hover": { color: "primary.main" },
            }}
          >
            {`${offRight.count} more ${offRight.count === 1 ? "run" : "runs"} →`}
          </ButtonBase>
        </Box>
      ) : null}
    </Box>
  );
});

ResultsGrid.propTypes = {
  rows: PropTypes.array,
  columnDefs: PropTypes.array,
  context: PropTypes.object,
  rowHeight: PropTypes.number,
  activeRowId: PropTypes.string,
  onOpenRow: PropTypes.func,
  onSelectionChanged: PropTypes.func,
  loading: PropTypes.bool,
  layout: PropTypes.object,
  /** P5: arrow keys moved the grid cursor while a row is open → open it. */
  onCursorRow: PropTypes.func,
};

export default ResultsGrid;
