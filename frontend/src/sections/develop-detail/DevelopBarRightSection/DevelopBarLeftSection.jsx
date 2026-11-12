import {
  Badge,
  Box,
  Button,
  IconButton,
  InputAdornment,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify/iconify";
import SvgColor from "src/components/svg-color/svg-color";
import { Events } from "src/utils/Mixpanel/EventNames";
import { trackEvent } from "src/utils/Mixpanel/mixpanel";
import { useDatasetOriginStore, useDevelopFilterStoreShallow } from "../states";
import { useParams } from "react-router";
// W5 grid seam G2: `{ } Expression` beside the toolbar's Filter.
import ExpressionFilterButton from "src/sections/common/filters/ExpressionFilterButton";
import { useQuery } from "@tanstack/react-query";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import SplitFilterButton from "../DataTab/Splits/SplitFilterButton";
import { FailedOnlyChip } from "../DataTab/DataGrid/ToolbarControls";
import { useDataGridView } from "../DataTab/DataGrid/useDataGridView";
import useDataToolbarView from "../DataTab/DataGrid/useDataToolbarView";
import useDataGridLayout from "../DataTab/DataGrid/useDataGridLayout";
import { useSearchParams } from "react-router-dom";
import { nextDensity, densityOf } from "src/sections/common/ux/DensityMenu";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { KeyCap } from "src/sections/common/ux/KeyCap";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { copyText } from "src/sections/common/ux/CopyButton";
import { notify } from "src/sections/common/ux/toast";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import useVersionUrl from "../DataTab/Versions/useVersionUrl";
import GoToRowPopover from "../DataTab/DataGrid/GoToRowPopover";
import CellPeekPopover from "../DataTab/DataGrid/CellPeekPopover";
import useRowViewUrl from "../DataTab/DatapointDrawerV2/useRowViewUrl";
import { isSurfaceShown } from "src/launch/launchSwitch";

// One view (A1-10): the toolbar's left half is what you look AT — search,
// splits, Failed only, Filter. How the grid looks (density, columns, saved
// views, Reset view) is the `View` menu on the right; version things live
// on the version chip in the header (`H` still opens the history).

const DevelopBarLeftSection = ({
  // Kept for callers; the density menu replaced the row-height resizer.
  resizerRef: _resizerRef,
  hideFilter = false,
  // Kept for callers: columns and density are the View menu's now.
  hideColumnView: _hideColumnView = false,
  hideSearch = false,
  hideRowHeight: _hideRowHeight = false,
}) => {
  const { dataset } = useParams();
  const { setDevelopFilterOpen, isFilterOpen } = useDevelopFilterStoreShallow(
    (s) => ({
      setDevelopFilterOpen: s.setDevelopFilterOpen,
      isFilterOpen: s.isDevelopFilterOpen,
    }),
  );
  const searchRef = useRef(null);
  const filterLabel = useMediaQuery("(min-width:1440px)");
  const [goToRow, setGoToRow] = useState(false);
  const [peek, setPeek] = useState(null); // {anchor, value, column, rowId}
  const { openRow } = useRowViewUrl();
  const [searchParams] = useSearchParams();
  const systemFiltersShown = isSurfaceShown("dataset.filtersSystem.pills");
  const { toggleHistory } = useVersionUrl();
  const { gridView, search, setSearchExtended, activeFilters } =
    useDataToolbarView(dataset);
  // The grid's column layout (`C` opens Columns in the View menu; ⌘K
  // `Columns…` / `Reset columns` are registered here, once per page).
  const layout = useDataGridLayout(dataset);

  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", { enabled: false }),
  );

  const { gridApi } = useDevelopDetailContext();

  const { processingComplete } = useDatasetOriginStore();

  const isData = Boolean(tableData?.data?.result?.table?.length);
  const failedRows = tableData?.data?.result?.metadata?.counts?.failed_rows;
  const isSyntheticDataset = Boolean(
    tableData?.data?.result?.synthetic_dataset,
  );
  const theme = useTheme();

  const disabledAll = !isData || (isSyntheticDataset && !processingComplete);
  const failedByEval =
    tableData?.data?.result?.metadata?.counts?.failed_by_eval;
  const versionNumber = tableData?.data?.result?.metadata?.version?.number;
  const { failedOnly: failedOnlyOn } = useDataGridView(dataset);

  const copyViewLink = async () => {
    const ok = await copyText(window.location.href);
    notify(
      ok
        ? "Link copied · includes search, filters and sort"
        : "Couldn't copy the link",
      {
        variant: ok ? "success" : "error",
        key: "copy-view-link",
      },
    );
  };

  // Grid keys (DG#30, MUST 6). Row keys (J/K/Enter) belong to the Row view
  // while a row is open; Enter on a cell is the grid's own (onCellKeyDown).
  const rowOpen = Boolean(searchParams.get("row"));
  const api = () => gridApi?.current?.api ?? gridApi?.current;
  const moveRow = (step) => {
    const g = api();
    if (!g?.getDisplayedRowCount) return;
    const cell = g.getFocusedCell?.();
    const count = g.getDisplayedRowCount();
    const next = Math.max(
      0,
      Math.min(count - 1, cell ? cell.rowIndex + step : 0),
    );
    const col =
      cell?.column ??
      g
        .getAllDisplayedColumns?.()
        .find((c) => !c.getColDef?.().checkboxSelection);
    g.ensureIndexVisible(next, "middle");
    if (col) g.setFocusedCell(next, col);
  };
  useShortcuts(
    "grid",
    {
      next: () => moveRow(1),
      prev: () => moveRow(-1),
      select: () => {
        const g = api();
        const cell = g?.getFocusedCell?.();
        const node = cell ? g.getDisplayedRowAtIndex(cell.rowIndex) : null;
        node?.setSelected?.(!node.isSelected());
      },
      peek: () => {
        const g = api();
        const cell = g?.getFocusedCell?.();
        if (!cell) return;
        const node = g.getDisplayedRowAtIndex(cell.rowIndex);
        const colId = cell.column.getColId();
        const anchor = document.querySelector(".ag-cell-focus");
        if (!node?.data || !anchor) return;
        setPeek({
          anchor,
          value: node.data[colId],
          column: cell.column.getColDef()?.headerName ?? colId,
          rowId: node.data.row_id,
        });
      },
      edit: () => {
        const g = api();
        const cell = g?.getFocusedCell?.();
        if (cell)
          g.startEditingCell({ rowIndex: cell.rowIndex, colKey: cell.column });
      },
      filter: () => !disabledAll && setDevelopFilterOpen((b) => !b),
      columns: () => !disabledAll && layout.openMenu(),
      density: () => {
        const d = nextDensity(gridView.density);
        gridView.setDensity(d);
        notify(`Density: ${densityOf(d).label}`, { key: "density" });
      },
      history: () => toggleHistory(),
      goToRow: () => !disabledAll && setGoToRow(true),
      search: () => searchRef.current?.focus(),
      escape: () => {
        const g = api();
        g?.deselectAll?.();
      },
    },
    // While the Row view is open its own keys (J/K, E, X, D, F, C, Esc)
    // win; `/` still reaches the search through G0's global fallback.
    { enabled: !hideSearch && !rowOpen },
  );
  useRegisterCommands(
    "dataset-grid",
    [
      {
        id: "grid.failed",
        label: "Show failed rows",
        keywords: ["failed only", "fails"],
        icon: "mdi:close-circle-outline",
        run: () => gridView.setFailedOnly(true),
        when: () => !disabledAll && Boolean(failedRows),
      },
      {
        id: "grid.goToRow",
        label: "Go to row…",
        shortcut: "goToRow",
        icon: "mdi:arrow-down-bold-box-outline",
        run: () => setGoToRow(true),
        when: () => !disabledAll,
      },
      {
        id: "grid.copyLink",
        label: "Copy link to this view",
        icon: "mdi:link-variant",
        run: copyViewLink,
      },
      {
        id: "grid.filter",
        label: "Filter rows…",
        keywords: ["filter", "conditions"],
        shortcut: "filter",
        icon: "mdi:filter-outline",
        run: () => setDevelopFilterOpen(true),
        when: () => !disabledAll,
      },
    ],
    [disabledAll, failedRows],
  );

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: theme.spacing(1),
        minHeight: 36,
        flexWrap: "nowrap",
        minWidth: 0,
      }}
    >
      {!hideSearch && (
        <FormSearchField
          size="small"
          placeholder="Search rows"
          searchQuery={search}
          onChange={(e) => setSearchExtended(e.target.value)}
          disabled={disabledAll}
          inputRef={searchRef}
          onKeyDown={(e) => {
            if (e.key !== "Escape") return;
            e.stopPropagation();
            if (search) setSearchExtended("");
            else e.currentTarget.querySelector("input")?.blur();
          }}
          inputProps={{ "aria-label": "Search rows", sx: { padding: 0 } }}
          sx={{
            // The main "look at" control gets the room (A1-10).
            width: { xs: "200px", lg: "260px", xl: "300px" },
            flexShrink: 0,
            "& .MuiInputBase-input": {
              paddingY: `${theme.spacing(0.5)}`,
              paddingRight: `${theme.spacing(0.5)}`,
            },
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SvgColor
                  src={`/assets/icons/custom/search.svg`}
                  sx={{ width: "20px", height: "20px", color: "text.disabled" }}
                />
              </InputAdornment>
            ),
            endAdornment: (
              <InputAdornment position="end">
                {search ? (
                  <IconButton
                    size="small"
                    aria-label="Clear search"
                    onClick={() => setSearchExtended("")}
                  >
                    <Iconify icon="solar:close-linear" width={16} />
                  </IconButton>
                ) : (
                  <KeyCap>/</KeyCap>
                )}
              </InputAdornment>
            ),
          }}
        />
      )}
      <SplitFilterButton datasetId={dataset} disabled={disabledAll} />
      {/* IT-R4 G5 P6: no chip before any evaluated run; kept while on so
          the filter can always be turned off. */}
      {/* L6 launch switch: Failed only and Expression are system filters
          dev's table read does not serve (dataset.filtersSystem.pills and
          dataset.filtersSystem.expressions, each its own claim). */}
      {systemFiltersShown &&
        (Boolean(failedRows) ||
          Boolean(failedByEval?.length) ||
          failedOnlyOn) && (
          <FailedOnlyChip
            datasetId={dataset}
            count={failedRows}
            evals={failedByEval}
            version={versionNumber}
            disabled={disabledAll}
          />
        )}
      {!hideFilter && (
        <KeyHintTooltip
          title={
            activeFilters
              ? `Filter · ${activeFilters} active`
              : "Filter rows by column values"
          }
          action="filter"
        >
          <span>
            <Button
              size="small"
              color="inherit"
              aria-label={
                activeFilters ? `Filter, ${activeFilters} active` : "Filter"
              }
              aria-pressed={isFilterOpen}
              disabled={disabledAll}
              data-develop-filter-anchor=""
              onClick={() => {
                trackEvent(Events.datasetFilterSelected);
                setDevelopFilterOpen((b) => !b);
              }}
              startIcon={
                <Badge
                  badgeContent={activeFilters}
                  color="primary"
                  invisible={!activeFilters}
                  sx={{
                    "& .MuiBadge-badge": {
                      fontSize: 10,
                      height: 16,
                      minWidth: 16,
                      px: 0.5,
                    },
                  }}
                >
                  <Iconify icon="mdi:filter-outline" width={18} />
                </Badge>
              }
              sx={{
                typography: "s2",
                color: "text.secondary",
                minWidth: 0,
                px: filterLabel ? 1 : 0.75,
                whiteSpace: "nowrap",
                flexShrink: 0,
                ...(isFilterOpen && { bgcolor: "action.selected" }),
                "& .MuiButton-startIcon": filterLabel ? undefined : { m: 0 },
              }}
            >
              {filterLabel ? "Filter" : null}
            </Button>
          </span>
        </KeyHintTooltip>
      )}
      {isSurfaceShown("dataset.filtersSystem.expressions") &&
        !hideFilter &&
        !disabledAll && (
          <ExpressionFilterButton datasetId={dataset} showLabel={filterLabel} />
        )}
      <CellPeekPopover
        peek={peek}
        onClose={() => setPeek(null)}
        onOpenRow={() => {
          const id = peek?.rowId;
          setPeek(null);
          if (id) openRow(id);
        }}
      />
      <GoToRowPopover
        open={goToRow}
        anchorEl={searchRef.current}
        onClose={() => setGoToRow(false)}
        total={tableData?.data?.result?.metadata?.total_rows}
        onGo={(n) => {
          const g = api();
          if (!g) return;
          g.ensureIndexVisible(n - 1, "middle");
          const col = g.getAllDisplayedColumns?.()[1];
          if (col) g.setFocusedCell(n - 1, col);
        }}
      />
    </Box>
  );
};

export default DevelopBarLeftSection;

DevelopBarLeftSection.propTypes = {
  resizerRef: PropTypes.object,
  hideFilter: PropTypes.bool,
  hideColumnView: PropTypes.bool,
  hideSearch: PropTypes.bool,
  hideRowHeight: PropTypes.bool,
};
