/* eslint-disable react/prop-types */
import { Box } from "@mui/material";
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
import { getRandomId, safeParse } from "src/utils/utils";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "src/routes/hooks";
import NumberQuickFilterPopover from "src/components/ComplexFilter/QuickFilterComponents/NumberQuickFilterPopover/NumberQuickFilterPopover";

import {
  AllowedGroups,
  applyQuickFilters,
  FILTER_FOR_HAS_EVAL,
  SPAN_DEFAULT_COLUMNS,
  mergeCellStyle,
  generateAnnotationColumnsForTracing,
} from "./common";
import CustomTraceRenderer from "./Renderers/CustomTraceRenderer";
import CustomTraceHeaderRenderer from "./Renderers/CustomTraceHeaderRenderer";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { statusBar } from "src/components/run-insights/traces-tab/common";
import { objectCamelToSnake } from "src/utils/utils";
import LLMTracingSpanDetailDrawer from "./LLMTracingSpanDetailDrawer";
import { useLLMTracingStoreShallow, useSpanGridStore } from "./states";
import { userTraceRowHeightMapping } from "../UsersView/common";
import IPOPTooltipComponent from "./Renderers/IPOPTooltipComponent";
import { RENDERER_CONFIG } from "./Renderers/common";
import { NameCell } from "./Renderers";
import IPOPCell from "./Renderers/IPOPCell";
import _ from "lodash";
import { isCellValueEmpty } from "src/components/table/utils";
import { APP_CONSTANTS } from "src/utils/constants";
import { useShallowToggleAnnotationsStore } from "../../agents/store";

const ROWS_LIMIT = 100;

// Normalize config object keys from snake_case to camelCase while preserving id values as snake_case
const normalizeConfigKeys = (config) =>
  config?.map((obj) => {
    const result = {};
    for (const [key, value] of Object.entries(obj)) {
      result[key.replace(/_([a-z])/g, (_, c) => c.toUpperCase())] = value;
    }
    return result;
  });

const getSpanListColumnDefs = (col) => {
  const colId = col?.id;
  const isInputOutput = colId === "input" || colId === "output";
  const isCustomColumn = col?.groupBy === "Custom Columns";

  return {
    headerName: col.name,
    ...(isCustomColumn
      ? { colId: col.id, minWidth: 180, flex: 1 }
      : { field: col.id }),
    hide: !col?.isVisible,
    col,
    // Custom columns use valueGetter to handle dot-notation attribute keys
    ...(isCustomColumn
      ? (() => {
          return {
            valueGetter: (params) => {
              if (!params.data) return null;
              let value = params.data[colId];
              if (value === undefined && colId.includes(".")) {
                value = colId
                  .split(".")
                  .reduce((obj, key) => obj?.[key], params.data);
              }
              if (value === undefined || value === null) return null;
              if (Array.isArray(value) || typeof value === "object") {
                return JSON.stringify(value);
              }
              return String(value);
            },
          };
        })()
      : isInputOutput
        ? {
            valueGetter: (params) => {
              const value = params.data?.[colId];
              if (isCellValueEmpty(value)) {
                return null;
              }
              if (typeof value === "object") {
                return JSON.stringify(value);
              }
              return value;
            },
          }
        : {}),
    valueFormatter: (params) => {
      const value = params.value;
      if (isCellValueEmpty(value)) {
        return "-"; // shown when no renderer is used
      }
      // For input/output columns, valueGetter already normalized the value
      // so we don't need to do anything here
      return value;
    },
    cellRendererSelector: (params) => {
      const value = params.value;
      if (isCellValueEmpty(value)) {
        // No renderer for empty values
        return null;
      }
      const column = params?.colDef?.col;
      const colId = column?.id;

      if (RENDERER_CONFIG.nameColumns.includes(colId)) {
        return {
          component: NameCell,
        };
      }
      if (colId === "input" || colId === "output") {
        return {
          component: IPOPCell,
        };
      }
      // Use CustomTraceRenderer for non-empty values
      return { component: CustomTraceRenderer };
    },
    cellStyle: (params) => {
      const value = params.value;
      if (isCellValueEmpty(value)) {
        return {
          display: "flex",
          alignItems: "center",
          height: "100%",
          justifyContent: "center",
        };
      }
    },
    headerComponent: CustomTraceHeaderRenderer,
    // Add tooltip for input/output columns
    ...(col?.id === "input" || col?.id === "output"
      ? {
          tooltipComponent: IPOPTooltipComponent,
          tooltipValueGetter: (params) => {
            const value = params.value;
            // Parse value according to its type - if string (JSON from valueGetter), parse to object
            // Otherwise return as is
            if (value === null || value === undefined || value === "") {
              return null;
            }
            // If value is a string, try to parse it (it might be a JSON string from valueGetter)
            if (typeof value === "string") {
              const parsed = safeParse(value);
              // If parsing succeeded and result is an object, use it; otherwise use original string
              return typeof parsed === "object" && parsed !== null
                ? parsed
                : value;
            }
            // If value is already an object, return it directly
            return value;
          },
        }
      : {}),
  };
};

const EMPTY_EXTRA_FILTERS = [];

const SpanGrid = React.forwardRef(
  (
    {
      columns,
      setColumns,
      filters,
      extraFilters,
      setFilters,
      setExtraFilters,
      setFilterOpen,
      setLoading,
      hasEvalFilter,
      cellHeight,
      metricFilters,
      pendingCustomColumnsRef,
      enabled = true,
    },
    gridRef,
  ) => {
    const { showMetricsIds, reset: resetMetricIds } =
      useShallowToggleAnnotationsStore((state) => ({
        showMetricsIds: state.showMetricsIds,
        reset: state.reset,
      }));

    const agTheme = useAgTheme();
    const { observeId } = useParams();
    const { setSpanDetailDrawerOpen } = useLLMTracingStoreShallow((state) => ({
      setSpanDetailDrawerOpen: state.setSpanDetailDrawerOpen,
    }));
    const [openQuickFilter, setOpenQuickFilter] = useState(null);
    const [selectedAll, setSelectedAll] = useState(false);

    // Use ref to track latest columns for comparison without triggering dataSource recreation
    const columnsRef = useRef(columns);
    useEffect(() => {
      columnsRef.current = columns;
    }, [columns]);

    // Prefetch cache: stores next page data so scroll feels instant
    const prefetchCache = useRef(new Map());

    const refreshGrid = () => {
      gridRef?.current?.api?.refreshServerSide();
    };

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

    // Grid Options
    const defaultColDef = useMemo(
      () => ({
        filter: false,
        resizable: true,
        suppressMovable: true,
        suppressHeaderMenuButton: true,
        suppressHeaderFilterButton: true,
        suppressHeaderContextMenu: true,
        sortable: false,
        minWidth: 200,
        flex: 1,
        cellStyle: {
          padding: 0,
          height: "100%",
          display: "flex",
          flex: 1,
          flexDirection: "column",
        },
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

    const { columnDefs } = useMemo(() => {
      // If no columns yet → return initial columnDefs
      if (!columns || columns.length === 0) {
        return {
          columnDefs: SPAN_DEFAULT_COLUMNS,
          bottomRow: [],
        };
      }

      // If columns are populated → process normally
      const grouping = {};
      const bottomRowObj = {};

      for (const eachCol of columns) {
        if (eachCol?.groupBy) {
          if (!grouping[eachCol?.groupBy]) {
            grouping[eachCol?.groupBy] = [eachCol];
          } else {
            grouping[eachCol?.groupBy].push(eachCol);
          }
        } else {
          grouping[getRandomId()] = [eachCol];
        }
      }
      const annotationColumns = generateAnnotationColumnsForTracing(
        grouping["Annotation Metrics"] || [],
        showMetricsIds,
      );
      delete grouping["Annotation Metrics"];
      const columnDefsResult = Object.entries(grouping).map(([group, cols]) => {
        if (!AllowedGroups.includes(group) && cols.length === 1) {
          const c = cols[0];
          bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
          return getSpanListColumnDefs(c);
        } else {
          return {
            headerName: group,
            children: cols.map((c) => {
              bottomRowObj[c?.id] = c?.average ? `Average ${c?.average}` : null;
              const colDef = getSpanListColumnDefs(c);
              return {
                ...colDef,
                minWidth: 200,
                flex: 1,
                cellStyle: mergeCellStyle(colDef, { paddingInline: 0 }),
              };
            }),
          };
        }
      });
      if (annotationColumns?.length > 0) {
        columnDefsResult.push(annotationColumns[0]);
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
                // Omit project_id when null — backend treats absent
                // project_id as org-scoped (used by user-detail page).
                ...(observeId ? { project_id: observeId } : {}),
                page_number: page,
                page_size: ROWS_LIMIT,
                filters: JSON.stringify([
                  ...objectCamelToSnake([
                    ...filters,
                    ...(hasEvalFilter ? [FILTER_FOR_HAS_EVAL] : []),
                  ]),
                  ...(extraFilters || EMPTY_EXTRA_FILTERS),
                  ...(metricFilters || []),
                ]),
              });

              // Use prefetched data if available, otherwise fetch
              const cached = prefetchCache.current.get(pageNumber);
              prefetchCache.current.delete(pageNumber);
              const results =
                cached ||
                (await axios.get(
                  endpoints.project.getSpansForObserveProject(),
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
                // don't keep retriggering the merge on every fetch.
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
                  // survives backend col changes.
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
              useSpanGridStore.setState({ totalRowCount: totalRows || 0 });

              // Infinite-scroll: don't expose total upfront → scrollbar grows as you scroll
              const isLastPage = rows.length < ROWS_LIMIT;
              const lastRow = isLastPage ? request.startRow + rows.length : -1;

              params.success({
                rowData: rows,
                rowCount: lastRow,
              });

              // Prefetch next page so scroll feels instant
              if (!isLastPage) {
                axios
                  .get(endpoints.project.getSpansForObserveProject(), {
                    params: buildParams(pageNumber + 1),
                  })
                  .then((res) => {
                    prefetchCache.current.set(pageNumber + 1, res);
                  })
                  .catch(() => {});
              }
            } catch {
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
      [
        filters,
        JSON.stringify(extraFilters),
        JSON.stringify(metricFilters),
        observeId,
        setLoading,
        hasEvalFilter,
        enabled,
      ],
    );

    const onSelectionChanged = useCallback((params) => {
      // Trust server-side selection state verbatim — [] is valid when
      // selectAll is true (no deselections). See TraceGrid for details.
      const isServerSide =
        typeof params.api.getServerSideSelectionState === "function";
      const ssState = isServerSide
        ? params.api.getServerSideSelectionState() || {}
        : {};
      const nodes = params.api.getSelectedNodes?.() || [];
      const idsFromNodes = nodes.map((n) => n.data?.span_id).filter(Boolean);
      const toggled = isServerSide ? ssState.toggledNodes || [] : idsFromNodes;
      useSpanGridStore.setState({
        toggledNodes: toggled,
        selectAll: !!ssState.selectAll,
      });
    }, []);

    const handleCellClick = useCallback(
      (event) => {
        if (!event?.node?.id) {
          //discard clicks on empty rows
          return;
        }
        if (event?.column?.colId === "status") {
          return;
        }
        if (
          event.column.getColId() === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
        ) {
          const selected = event.node.isSelected();
          event.node.setSelected(!selected);
          // Belt-and-suspenders: sync store directly (see TraceGrid note).
          setTimeout(() => {
            const isServerSide =
              typeof event.api.getServerSideSelectionState === "function";
            const ssState = isServerSide
              ? event.api.getServerSideSelectionState() || {}
              : {};
            const nodes = event.api.getSelectedNodes?.() || [];
            const idsFromNodes = nodes
              .map((n) => n.data?.span_id)
              .filter(Boolean);
            const toggled = isServerSide
              ? ssState.toggledNodes || []
              : idsFromNodes;
            useSpanGridStore.setState({
              toggledNodes: toggled,
              selectAll: !!ssState.selectAll,
            });
          }, 0);
          return;
        }

        const traceId = event?.data?.trace_id;
        const spanId = event?.data?.span_id;
        if (!traceId || !spanId) {
          return;
        }
        setSpanDetailDrawerOpen({
          trace_id: traceId,
          span_id: spanId,
          filters: filters,
          fromSpansView: true,
        });

        trackEvent(Events.observeSpanidClicked);
      },
      [filters, setSpanDetailDrawerOpen],
    );

    useEffect(() => {
      return () => resetMetricIds();
    }, [resetMetricIds]);
    return (
      <Box sx={{ height: "calc(100vh - 270px)" }}>
        <AgGridReact
          className={
            cellHeight && cellHeight !== "Short"
              ? "cell-wrap clean-data-table"
              : "clean-data-table"
          }
          // rowSelection={{ mode: "multiRow" }}
          rowHeight={userTraceRowHeightMapping[cellHeight]?.height ?? 40}
          theme={agTheme.withParams({
            columnBorder: false,
            headerColumnBorder: { width: 0 },
            wrapperBorder: { width: 0 },
            wrapperBorderRadius: 0,
          })}
          ref={gridRef}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          rowSelection={{ mode: "multiRow" }}
          pagination={false}
          cacheBlockSize={ROWS_LIMIT}
          maxBlocksInCache={undefined}
          rowBuffer={10}
          suppressRowClickSelection={true}
          rowModelType="serverSide"
          tooltipShowDelay={0}
          tooltipHideDelay={2000}
          tooltipInteraction={true}
          serverSideDatasource={dataSource}
          suppressServerSideFullWidthLoadingRow={true}
          serverSideInitialRowCount={ROWS_LIMIT}
          onCellClicked={handleCellClick}
          onSelectionChanged={onSelectionChanged}
          // onGridReady={(params) => {
          //   timeoutRef.current = setTimeout(() => {
          //     params.api.sizeColumnsToFit([
          //       "latencyMs", "latency", "totalCost", "status", "totalCost", "cost", "totalTokens"
          //     ]);
          //   }, 200);
          // }}
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
          // suppressColumnMoveAnimation={true}
          // suppressColumnVirtualisation={true}
          statusBar={statusBar}
          blockLoadDebounceMillis={300}
          getRowId={(d) => {
            return d?.data?.span_id;
          }}
        />
        <LLMTracingSpanDetailDrawer refreshGrid={refreshGrid} />
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

SpanGrid.displayName = "SpanGrid";

SpanGrid.propTypes = {
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  filters: PropTypes.array,
  extraFilters: PropTypes.array,
  setFilters: PropTypes.func,
  setFilterOpen: PropTypes.bool,
  setLoading: PropTypes.func,
  setPageMap: PropTypes.func,
  compareType: PropTypes.string,
  hasEvalFilter: PropTypes.bool,
  cellHeight: PropTypes.string,
  metricFilters: PropTypes.array,
  enabled: PropTypes.bool,
};

export default SpanGrid;
