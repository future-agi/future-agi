/* eslint-disable react/prop-types */
import { Box, Typography, useTheme } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/styles/clean-data-table.css";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAgTheme } from "src/hooks/use-ag-theme";
import axios, { endpoints } from "src/utils/axios";
import { getRandomId } from "src/utils/utils";
import NumberQuickFilterPopover from "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover";
import NoRowsOverlay from "src/sections/project-detail/CompareDrawer/NoRowsOverlay";
import {
  AllowedGroups,
  applyQuickFilters,
  TRACE_DEFAULT_COLUMNS,
  getTraceListColumnDefs,
  FILTER_FOR_HAS_EVAL,
  generateAnnotationColumnsForTracing,
} from "./common";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { userTraceRowHeightMapping } from "../UsersView/common";
import { statusBar } from "src/components/run-insights/traces-tab/common";
import { objectCamelToSnake } from "src/utils/utils";
import { canonicalizeApiFilterColumnIds } from "src/utils/filter-column-ids";
import LLMTracingTraceDetailDrawer from "./LLMTracingTraceDetailDrawer";
import { useLLMTracingStoreShallow, useTraceGridStore } from "./states";
import _ from "lodash";
import { APP_CONSTANTS } from "src/utils/constants";
import { useReplaySessionsStoreShallow } from "../SessionsView/ReplaySessions/store";
import { REPLAY_MODULES } from "../SessionsView/ReplaySessions/configurations";
import { useShallowToggleAnnotationsStore } from "../../agents/store";

const ROWS_LIMIT = 100;
const EMPTY_EXTRA_FILTERS = [];

// Normalize config object keys from snake_case to camelCase while preserving id values as snake_case
const normalizeConfigKeys = (config) =>
  config?.map((obj) => {
    const result = {};
    for (const [key, value] of Object.entries(obj)) {
      result[key.replace(/_([a-z])/g, (_, c) => c.toUpperCase())] = value;
    }
    return result;
  });

const TraceGrid = React.forwardRef(
  (
    {
      filters,
      extraFilters,
      columns,
      setColumns,
      setFilters,
      setExtraFilters,
      setFilterOpen,
      setLoading,
      projectId,
      cellHeight,
      hasEvalFilter,
      metricFilters,
      pendingCustomColumnsRef,
      enabled = true,
      showErrors = false,
    },
    gridRef,
  ) => {
    const agTheme = useAgTheme();
    const theme = useTheme();
    const [dateInterval] = useUrlState("dateInterval", "day");
    const { openReplaySessionDrawer, currentStep, validatedSteps } =
      useReplaySessionsStoreShallow((state) => ({
        openReplaySessionDrawer: state.openReplaySessionDrawer,
        currentStep: state.currentStep,
        validatedSteps: state.validatedSteps,
      }));

    const {
      traceDetailDrawerOpen,
      setTraceDetailDrawerOpen,
      setVisibleTraceIds,
    } = useLLMTracingStoreShallow((state) => ({
      traceDetailDrawerOpen: state.traceDetailDrawerOpen,
      setTraceDetailDrawerOpen: state.setTraceDetailDrawerOpen,
      setVisibleTraceIds: state.setVisibleTraceIds,
    }));
    const activeTraceId = traceDetailDrawerOpen?.traceId || null;
    const [openQuickFilter, setOpenQuickFilter] = useState(null);
    const [selectedAll, setSelectedAll] = useState(false);

    // Use ref to track latest columns for comparison without triggering dataSource recreation
    const columnsRef = useRef(columns);
    useEffect(() => {
      columnsRef.current = columns;
    }, [columns]);

    // Prefetch cache: stores next page data so scroll feels instant
    const prefetchCache = useRef(new Map());
    const { showMetricsIds, reset: resetMetricIds } =
      useShallowToggleAnnotationsStore((state) => ({
        showMetricsIds: state.showMetricsIds,
        reset: state.reset,
      }));
    const refreshGrid = useCallback(() => {
      gridRef?.current?.api?.refreshServerSide({ purge: true });
    }, [gridRef]);

    // Listen for refresh events from the header reload button
    useEffect(() => {
      const handler = () => refreshGrid();
      window.addEventListener("observe-refresh", handler);
      return () => window.removeEventListener("observe-refresh", handler);
    }, [refreshGrid]);

    // Clear AG Grid's internal selection when the project changes — the
    // zustand reset handled in the header only clears our mirror, not AG
    // Grid's server-side selection model. Also reset the local
    // `selectedAll` flag so the header checkbox's next click re-triggers
    // selectAll (otherwise the stale `true` makes the first click a
    // deselect no-op).
    useEffect(() => {
      const handler = () => {
        gridRef?.current?.api?.deselectAll?.();
        setSelectedAll(false);
      };
      window.addEventListener("observe-reset-selection", handler);
      return () =>
        window.removeEventListener("observe-reset-selection", handler);
    }, [gridRef]);

    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        filter: false,
        resizable: true,
        suppressHeaderMenuButton: true,
        suppressHeaderFilterButton: true,
        suppressHeaderContextMenu: true,
        suppressMovable: false,
        flex: 1,
        minWidth: 80,
        cellStyle: {
          padding: 0,
          height: "100%",
          display: "flex",
          flex: 1,
          flexDirection: "column",
        },
        suppressSizeToFit: false,
        sortable: false,
        cellRendererParams: {
          applyQuickFilters: applyQuickFilters(
            setExtraFilters,
            setOpenQuickFilter,
            setFilterOpen,
          ),
        },
      }),
      [setFilterOpen, setExtraFilters],
    );

    const dataSource = useMemo(
      () => {
        prefetchCache.current.clear();
        return {
          getRows: async (params) => {
            if (!enabled) {
              params.success({ rowData: [], rowCount: 0 });
              return;
            }
            try {
              setLoading(true);
              const { request } = params;

              const pageSize = request.endRow - request.startRow;
              const pageNumber = Math.floor(request.startRow / pageSize);

              const buildParams = (page) => ({
                // Omit project_id when null — the backend treats absent
                // project_id as org-scoped (used by the cross-project user
                // detail page).
                ...(projectId ? { project_id: projectId } : {}),
                page_number: page,
                page_size: ROWS_LIMIT,
                filters: JSON.stringify(
                  canonicalizeApiFilterColumnIds([
                    ...objectCamelToSnake([
                      ...filters,
                      ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
                    ]),
                    ...(extraFilters || EMPTY_EXTRA_FILTERS),
                    ...(metricFilters || []),
                  ]),
                ),
                ...(dateInterval && { interval: dateInterval }),
              });

              // Use prefetched data if available, otherwise fetch
              const cached = prefetchCache.current.get(pageNumber);
              prefetchCache.current.delete(pageNumber);
              const results =
                cached ||
                (await axios.get(
                  endpoints.project.getTracesForObserveProject(),
                  { params: buildParams(pageNumber) },
                ));

              const res = results?.data?.result;
              const newCols = normalizeConfigKeys(res?.config);

              // Use ref to get latest columns for comparison without triggering dataSource recreation
              // Compare only non-custom columns to avoid unnecessary re-renders
              if (newCols) {
                const currentNonCustom = (columnsRef.current || []).filter(
                  (c) => c.groupBy !== "Custom Columns",
                );
                const existingCustom = (columnsRef.current || []).filter(
                  (c) => c.groupBy === "Custom Columns",
                );
                const pending = pendingCustomColumnsRef?.current || [];
                const existingIds = new Set(existingCustom.map((c) => c.id));
                const dedupedPending = pending.filter(
                  (c) => !existingIds.has(c.id),
                );
                // Strip isVisible from the diff so saved-view hide maps
                // don't keep retriggering the merge on every fetch. The
                // hasPending clause ensures a saved-view switch with same
                // backend cols still drains the pending customs.
                const stripVis = (cols) =>
                  (cols || []).map(({ isVisible, ...rest }) => rest);
                const backendChanged = !_.isEqual(
                  stripVis(newCols),
                  stripVis(currentNonCustom),
                );
                const hasPending = dedupedPending.length > 0;
                if (backendChanged || hasPending) {
                  const allCustom = [...existingCustom, ...dedupedPending];
                  if (pending.length > 0 && pendingCustomColumnsRef) {
                    pendingCustomColumnsRef.current = [];
                  }
                  // Preserve existing isVisible so saved-view hide intent
                  // survives backend col changes. Pending-only path reuses
                  // currentNonCustom to keep column identity stable.
                  const finalNonCustom = backendChanged
                    ? newCols.map((nc) => {
                        const existing = currentNonCustom.find(
                          (c) => c.id === nc.id,
                        );
                        return existing
                          ? { ...nc, isVisible: existing.isVisible }
                          : nc;
                      })
                    : currentNonCustom;
                  setColumns(
                    allCustom.length > 0
                      ? [...finalNonCustom, ...allCustom]
                      : finalNonCustom,
                  );
                }
              }

              const rows = res?.table || [];
              const totalRows = res?.metadata?.total_rows;
              params.api.totalRowCount = totalRows;
              useTraceGridStore.setState({ totalRowCount: totalRows || 0 });

              // Infinite-scroll behavior: don't tell AG Grid the total upfront.
              // Use -1 (unknown) so it only extends the scrollbar as pages load.
              // When we get fewer rows than requested, that's the last page.
              const isLastPage = rows.length < ROWS_LIMIT;
              const lastRow = isLastPage ? request.startRow + rows.length : -1;

              params.success({
                rowData: rows,
                rowCount: lastRow,
              });

              if (pageNumber === 0 && rows.length === 0) {
                params.api?.showNoRowsOverlay();
              } else {
                params.api?.hideOverlay();
              }

              // Collect all loaded trace IDs for prev/next navigation
              setTimeout(() => {
                const ids = [];
                params.api.forEachNode((node) => {
                  if (node.data?.trace_id) ids.push(node.data.trace_id);
                });
                if (ids.length > 0) setVisibleTraceIds(ids);
              }, 0);

              // Prefetch next page so scroll feels instant
              if (!isLastPage) {
                axios
                  .get(endpoints.project.getTracesForObserveProject(), {
                    params: buildParams(pageNumber + 1),
                  })
                  .then((res) => {
                    prefetchCache.current.set(pageNumber + 1, res);
                  })
                  .catch(() => {});
              }
            } catch (error) {
              params.fail();
            } finally {
              setLoading(false);
            }
          },
        };
      },
      // Using columnsRef for comparison to avoid adding columns to deps
      // which would cause dataSource recreation on visibility changes
      // eslint-disable-next-line react-hooks/exhaustive-deps
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [
        filters,
        JSON.stringify(extraFilters),
        JSON.stringify(metricFilters),
        projectId,
        setLoading,
        hasEvalFilter,
        enabled,
        dateInterval,
      ],
    );

    const { columnDefs } = useMemo(() => {
      // If columns are empty → return initial/default columnDefs
      if (!columns || columns.length === 0) {
        return {
          columnDefs: TRACE_DEFAULT_COLUMNS,
          bottomRow: [],
        };
      }

      // Flat columns — no grouping for eval/annotation metrics
      const bottomRowObj = {};
      const annotationCols = columns.filter(
        (c) => c?.groupBy === "Annotation Metrics",
      );
      const customCols = columns.filter((c) => c?.groupBy === "Custom Columns");
      const otherCols = columns.filter(
        (c) =>
          c?.groupBy !== "Annotation Metrics" &&
          c?.groupBy !== "Custom Columns",
      );

      // Build flat column defs for non-annotation, non-custom columns
      const columnDefsResult = otherCols.map((c) => {
        bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
        return getTraceListColumnDefs(c);
      });

      // Group custom columns under a "Custom Columns" header (TH-4151)
      if (customCols.length > 0) {
        columnDefsResult.push({
          headerName: "Custom Columns",
          children: customCols.map((c) => {
            bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
            const colDef = getTraceListColumnDefs(c);
            return {
              ...colDef,
              minWidth: 200,
              flex: 1,
            };
          }),
        });
      }

      // Add annotation columns as flat columns (not grouped)
      const annotationColumns = generateAnnotationColumnsForTracing(
        annotationCols,
        showMetricsIds,
      );
      if (annotationColumns?.length > 0) {
        // Flatten: extract children from annotation groups
        for (const group of annotationColumns) {
          if (group.children) {
            columnDefsResult.push(...group.children);
          } else {
            columnDefsResult.push(group);
          }
        }
      }
      return {
        columnDefs: columnDefsResult,
        bottomRow: [
          {
            ...bottomRowObj,
          },
        ],
      };
    }, [columns, showMetricsIds]);

    useEffect(() => {
      return () => resetMetricIds();
    }, [resetMetricIds]);

    const onColumnMoved = useCallback(
      (params) => {
        if (!params.finished) return;

        const newOrder = params.api
          .getColumnState()
          .map((s) => s.colId)
          .filter((id) => id !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN);

        const byId = new Map(columns.map((c) => [c.id, c]));
        const reordered = newOrder.map((id) => byId.get(id)).filter(Boolean);
        const matched = new Set(newOrder);
        const unmatched = columns.filter((c) => !matched.has(c.id));
        const next = [...reordered, ...unmatched];

        const changed =
          next.length !== columns.length ||
          next.some((c, i) => c.id !== columns[i]?.id);
        if (changed) setColumns(next);
      },
      [columns, setColumns],
    );
    const onSelectionChanged = useCallback((params) => {
      // In server-side row model, ssState.toggledNodes is authoritative —
      // an empty array is a valid, meaningful state (e.g. when selectAll is
      // true, [] means "no deselections, everything is selected"). Only
      // fall back to getSelectedNodes() in client-side mode.
      const isServerSide =
        typeof params.api.getServerSideSelectionState === "function";
      const ssState = isServerSide
        ? params.api.getServerSideSelectionState() || {}
        : {};
      const selectedNodes = params.api.getSelectedNodes?.() || [];
      const idsFromNodes = selectedNodes
        .map((n) => n.data?.trace_id)
        .filter(Boolean);
      const toggled = isServerSide ? ssState.toggledNodes || [] : idsFromNodes;
      useTraceGridStore.setState({
        toggledNodes: toggled,
        selectAll: !!ssState.selectAll,
      });
    }, []);

    const handleCellClick = useCallback(
      (event) => {
        if (!event?.node?.id) {
          //disguard clicks on empty rows
          return;
        }
        if (event?.column?.colId === "status") return;
        if (
          event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
        ) {
          const selected = event.node.isSelected();
          event.node.setSelected(!selected);
          // Belt-and-suspenders: AG Grid v32+'s new rowSelection API can
          // silently drop the selectionChanged event when node.setSelected
          // is called manually in a serverSide row model. Mirror the
          // onSelectionChanged logic — trust server-side state verbatim so
          // toggling under selectAll correctly inverts the selection.
          setTimeout(() => {
            const isServerSide =
              typeof event.api.getServerSideSelectionState === "function";
            const ssState = isServerSide
              ? event.api.getServerSideSelectionState() || {}
              : {};
            const nodes = event.api.getSelectedNodes?.() || [];
            const idsFromNodes = nodes
              .map((n) => n.data?.trace_id)
              .filter(Boolean);
            const toggled = isServerSide
              ? ssState.toggledNodes || []
              : idsFromNodes;
            useTraceGridStore.setState({
              toggledNodes: toggled,
              selectAll: !!ssState.selectAll,
            });
          }, 0);
          return;
        }

        const traceId = event?.data?.trace_id;
        if (!traceId) {
          return;
        }
        setTraceDetailDrawerOpen({ traceId: traceId, filters: filters });

        // trackEvent(Events.observeTraceidClicked);
      },
      [filters, setTraceDetailDrawerOpen],
    );

    const shouldDisable = useMemo(() => {
      return (
        openReplaySessionDrawer?.[REPLAY_MODULES.TRACES] &&
        currentStep > 0 &&
        validatedSteps[currentStep - 1]
      );
    }, [openReplaySessionDrawer, currentStep, validatedSteps]);

    return (
      <Box
        sx={{ height: "calc(100vh - 270px)" }}
        className={cellHeight && cellHeight !== "Short" ? "cell-wrap" : ""}
      >
        <AgGridReact
          className={`clean-data-table ${shouldDisable ? "ag-grid-disabled" : ""}`}
          theme={agTheme.withParams({
            columnBorder: false,
            headerColumnBorder: false,
            wrapperBorder: { width: 0 },
            wrapperBorderRadius: 0,
            rowBorder: { width: 1, color: "rgba(0,0,0,0.06)" },
            headerFontSize: "13px",
            headerFontWeight: 500,
            headerBackgroundColor: "transparent",
            headerTextColor: theme.palette.text.primary,
            rowHoverColor: "rgba(120,87,252,0.04)",
          })}
          animateRows={false}
          headerHeight={40}
          ref={gridRef}
          rowHeight={userTraceRowHeightMapping[cellHeight]?.height ?? 40}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          tooltipShowDelay={0}
          tooltipHideDelay={2000}
          tooltipInteraction={true}
          rowSelection={{ mode: "multiRow" }}
          pagination={false}
          cacheBlockSize={ROWS_LIMIT}
          maxBlocksInCache={undefined}
          rowBuffer={10}
          suppressServerSideFullWidthLoadingRow={true}
          serverSideInitialRowCount={ROWS_LIMIT}
          suppressRowClickSelection={true}
          rowModelType="serverSide"
          serverSideDatasource={dataSource}
          noRowsOverlayComponent={() =>
            NoRowsOverlay(
              <Typography
                sx={{
                  fontSize: 14,
                  fontWeight: 400,
                  color: "text.secondary",
                }}
              >
                {showErrors ? "No error found" : "No traces found"}
              </Typography>,
            )
          }
          onCellClicked={handleCellClick}
          onSelectionChanged={onSelectionChanged}
          onColumnMoved={onColumnMoved}
          onColumnHeaderClicked={(event) => {
            if (event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
              return;
            }

            if (selectedAll) {
              event.api.deselectAll();
              setSelectedAll(false);
            } else {
              event.api.selectAll();
              setSelectedAll(true);
            }
          }}
          statusBar={statusBar}
          blockLoadDebounceMillis={300}
          getRowId={(d) => {
            return d?.data?.trace_id;
          }}
          getRowStyle={(params) => {
            if (
              params.data?.trace_id &&
              params.data.trace_id === activeTraceId
            ) {
              return { backgroundColor: "rgba(120, 87, 252, 0.08)" };
            }
            return null;
          }}
        />
        <LLMTracingTraceDetailDrawer refreshGrid={refreshGrid} />
        <NumberQuickFilterPopover
          open={Boolean(openQuickFilter)}
          filterData={openQuickFilter}
          onClose={() => setOpenQuickFilter(null)}
          setFilters={setFilters}
          setFilterOpen={setFilterOpen}
        />
      </Box>
    );
  },
);

TraceGrid.displayName = "TraceGrid";

TraceGrid.propTypes = {
  filters: PropTypes.array,
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  setFilters: PropTypes.func,
  setFilterOpen: PropTypes.bool,
  setLoading: PropTypes.func,
  compareType: PropTypes.string,
  projectId: PropTypes.string,
  cellHeight: PropTypes.string,
  hasEvalFilter: PropTypes.bool,
  metricFilters: PropTypes.array,
  enabled: PropTypes.bool,
  showErrors: PropTypes.bool,
};

export default TraceGrid;
