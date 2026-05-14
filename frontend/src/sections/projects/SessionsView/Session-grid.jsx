import { Box, LinearProgress, Skeleton, useTheme } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/styles/clean-data-table.css";
import React, {
  useMemo,
  useRef,
  useState,
  useEffect,
  useCallback,
} from "react";
import _ from "lodash";
import PropTypes from "prop-types";
import { getRandomId } from "src/utils/utils";
import TotalRowsStatusBar from "src/sections/develop-detail/Common/TotalRowsStatusBar";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import TracesDrawer from "../TracesDrawer/TracesDrawer";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { getSessionListColumnDef } from "./common";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { userTraceRowHeightMapping } from "../UsersView/common";
import { objectCamelToSnake } from "src/utils/utils";
import { canonicalizeApiFilterColumnIds } from "src/utils/filter-column-ids";
import { useSessionsGridStoreShallow } from "./ReplaySessions/store";
import { APP_CONSTANTS } from "src/utils/constants";

const SESSION_GRID_THEME_PARAMS = {
  columnBorder: false,
  rowVerticalPaddingScale: 2.6,
  headerColumnBorder: { width: 0 },
  wrapperBorder: { width: 0 },
  wrapperBorderRadius: 0,
};

const DATASET_ROWS_LIMIT = 30;

// Normalize config object keys from snake_case to camelCase while preserving id values as snake_case
const normalizeConfigKeys = (config) =>
  config?.map((obj) => {
    const result = {};
    for (const [key, value] of Object.entries(obj)) {
      result[key.replace(/_([a-z])/g, (_, c) => c.toUpperCase())] = value;
    }
    return result;
  });

const LoadingHeader = () => {
  return <Skeleton variant="text" width={100} height={20} />;
};

const SessionGrid = React.forwardRef(
  (
    {
      updateObj,
      columns,
      setColumns,
      filters,
      projectId,
      cellHeight,
      onSelectionChanged,
      className,
      onGridReady,
      pendingCustomColumnsRef,
      isOnSavedView = false,
    },
    gridApiRef,
  ) => {
    const [open, setOpen] = useState(false);
    const [currentRowData, setCurrentRowData] = useState(null);
    const theme = useTheme();
    const agTheme = useAgThemeWith(SESSION_GRID_THEME_PARAMS);
    const handleDrawerClose = () => {
      setOpen(false);
    };

    const { toggledNodes, selectAll } = useSessionsGridStoreShallow((s) => ({
      totalRowCount: s.totalRowCount,
      toggledNodes: s.toggledNodes,
      selectAll: s.selectAll,
    }));

    // Track latest columns via ref to avoid recreating dataSource on visibility changes
    const columnsRef = useRef(columns);
    useEffect(() => {
      columnsRef.current = columns;
    }, [columns]);

    // Same trick for updateObj + isOnSavedView — dataSource closes over them
    // once when memoized, but getRows fires on every scroll/refetch and needs
    // the latest values. On a saved view we filter columns by `updateObj`
    // (the view's visibleColumns); on a default tab we fall through to
    // `res.config.isVisible` (the backend's per-project saved visibility).
    // Without this gate the data-fetch overwrites the saved view's columns
    // with the project default on every page load.
    const updateObjRef = useRef(updateObj);
    useEffect(() => {
      updateObjRef.current = updateObj;
    }, [updateObj]);

    const isOnSavedViewRef = useRef(isOnSavedView);
    useEffect(() => {
      isOnSavedViewRef.current = isOnSavedView;
    }, [isOnSavedView]);

    // Mirror columnDefs into a ref so the dataSource's getRows always reads
    // the latest. Without this, the dataSource memo (deps =
    // [filters, projectId, dateInterval]) captures columnDefs ONCE — when
    // columns is still []. That initial columnDefs uses the LoadingHeader
    // skeleton branch (line 107-117), and every subsequent getRows call
    // (page scroll, sort, etc.) writes those skeletons into filteredColumnDefs,
    // causing the header skeletons to flash back in randomly even after
    // proper headers had loaded.
    const columnDefsRef = useRef([]);

    const [dateInterval] = useUrlState("dateInterval", "day");

    // Grid Options
    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        filter: false,
        resizable: true,
        suppressSizeToFit: false,
        cellStyle: {
          padding: "0px 20px",
          fontSize: "14px",
          height: "100%",
        },
      }),
      [],
    );

    const { columnDefs } = useMemo(() => {
      // Case 1: If no columns fetched yet → Return initial default columnDefs
      if (!columns || columns.length === 0) {
        return {
          columnDefs: Object.keys(updateObj).map((title) => ({
            headerComponent: LoadingHeader,
            field: title,
            minWidth: 200,
            flex: 1,
          })),
          bottomRow: [],
        };
      }

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

      const columnDefsResult = Object.entries(grouping).map(([group, cols]) => {
        if (cols.length === 1) {
          const c = cols[0];
          bottomRowObj[c?.id] = c?.average ? `${c?.average}` : null;
          return getSessionListColumnDef(c);
        } else {
          return {
            headerName: group,
            children: cols.map((c) => {
              bottomRowObj[c?.id] = c?.average ? `Average ${c?.average}` : null;
              return getSessionListColumnDef(c);
            }),
          };
        }
      });

      return {
        columnDefs: columnDefsResult,
        bottomRow: [
          {
            ...bottomRowObj,
          },
        ],
      };
    }, [columns, updateObj]);

    useEffect(() => {
      columnDefsRef.current = columnDefs;
    }, [columnDefs]);

    const [filteredColumnDefs, setFilteredColumnDefs] = useState([]);

    // Prefetch cache: stores next page data so scroll feels instant
    const prefetchCache = useRef(new Map());

    const dataSource = useMemo(
      () => {
        prefetchCache.current.clear();
        return {
          getRows: async (params) => {
            try {
              const { request } = params;

              const pageNumber = Math.floor(
                request.startRow / DATASET_ROWS_LIMIT,
              );

              const buildParams = (page) => ({
                // Omit project_id when null — backend treats absent
                // project_id as org-scoped (used by the cross-project
                // user detail page).
                ...(projectId ? { project_id: projectId } : {}),
                page_number: page,
                page_size: DATASET_ROWS_LIMIT,
                sort_params: JSON.stringify(
                  request?.sortModel?.map(({ colId, sort }) => ({
                    column_id: colId,
                    direction: sort,
                  })),
                ),
                filters: JSON.stringify(
                  canonicalizeApiFilterColumnIds(objectCamelToSnake(filters)),
                ),
                ...(dateInterval && { interval: dateInterval }),
              });

              // Use prefetched data if available, otherwise fetch
              const cached = prefetchCache.current.get(pageNumber);
              prefetchCache.current.delete(pageNumber);
              const results =
                cached ||
                (await axios.get(endpoints.project.projectSessionList(), {
                  params: buildParams(pageNumber),
                }));
              const res = results?.data?.result;
              const newCols = normalizeConfigKeys(res?.config);

              // Merge: preserve custom columns that the backend doesn't know about
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
                const backendChanged = !_.isEqual(newCols, currentNonCustom);
                // hasPending ensures same-tab saved-view clicks still drain
                // queued customs even when backend cols match.
                const hasPending = dedupedPending.length > 0;
                if (backendChanged || hasPending) {
                  const allCustom = [...existingCustom, ...dedupedPending];
                  if (pending.length > 0 && pendingCustomColumnsRef) {
                    pendingCustomColumnsRef.current = [];
                  }
                  const finalNonCustom = backendChanged
                    ? newCols
                    : currentNonCustom;
                  setColumns(
                    allCustom.length > 0
                      ? [...finalNonCustom, ...allCustom]
                      : finalNonCustom,
                  );
                }
              }

              // Read columnDefs from the ref so this filter always sees the
              // post-setColumns value, not the skeleton-headed default
              // captured when dataSource was first memoized. Without the ref,
              // every page-2+ scroll fetch wrote stale skeleton headers back
              // into filteredColumnDefs.
              const currentColumnDefs = columnDefsRef.current ?? columnDefs;
              const filteredColumns = currentColumnDefs.filter((column) => {
                // Grouped columns (e.g. Annotation Metrics) always visible
                if (column.children) return true;
                if (!column.field) return true;

                // On a saved view, the view's visibleColumns (carried in
                // updateObj) is the source of truth — ignore the backend's
                // per-project default so view-specific hides don't get
                // overwritten by the data-fetch response.
                if (isOnSavedViewRef.current) {
                  return updateObjRef.current?.[column.field] ?? true;
                }

                const columnConfig = (res?.config || []).find(
                  (config) => config.id === column.field,
                );
                return columnConfig ? columnConfig.isVisible : true;
              });

              setFilteredColumnDefs(filteredColumns);
              const rows = res?.table || [];
              const totalRows = res?.metadata?.total_rows;
              params.api.totalRowCount = totalRows;

              const isLastPage = rows.length < DATASET_ROWS_LIMIT;
              const lastRow = isLastPage ? request.startRow + rows.length : -1;

              params.success({
                rowData: rows,
                rowCount: lastRow,
              });

              // Prefetch next page so scroll feels instant
              if (!isLastPage) {
                axios
                  .get(endpoints.project.projectSessionList(), {
                    params: buildParams(pageNumber + 1),
                  })
                  .then((res) => {
                    prefetchCache.current.set(pageNumber + 1, res);
                  })
                  .catch(() => {});
              }
            } catch (error) {
              const message =
                (typeof error?.result === "string" && error?.result) ||
                error?.message ||
                "Failed to load sessions. Please check your filters.";
              enqueueSnackbar(message, { variant: "error" });
              params.success({ rowData: [], rowCount: 0 });
            }
          },
          getRowId: ({ data }) => {
            return data.session_id;
          },
        };
      },
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [filters, projectId, dateInterval],
    );

    const [finalColumnDefs, setFinalColumnDefs] = useState([]);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
      setIsLoading(true);
      setFinalColumnDefs(filteredColumnDefs);
      setIsLoading(false);
    }, [filteredColumnDefs]);

    useEffect(() => {
      if (columnDefs.length > 0) {
        setIsLoading(true);
        const updatedColumns = columnDefs.filter((col) => {
          // Grouped columns (e.g. Annotation Metrics) don't have a field
          if (col.children) return true;
          // New columns not yet in updateObj default to visible
          return updateObj[col.field] ?? true;
        });
        setFinalColumnDefs(updatedColumns);
        setIsLoading(false);
      }
    }, [updateObj, columnDefs]);

    const [statusBar] = useState({
      statusPanels: [
        {
          statusPanel: TotalRowsStatusBar,
          align: "left",
        },
      ],
    });

    const onColumnMoved = useCallback(
      (params) => {
        if (!params.finished) return;

        const newOrder = params.api
          .getColumnState()
          .map((s) => s.colId)
          .filter((id) => id !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN);

        if (!columns || !Array.isArray(columns)) return;

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

    const onRowClicked = (event) => {
      if (!event.data) {
        return;
      }

      setCurrentRowData(event.data);
      setOpen(true);
      trackEvent(Events.observeSessionidClicked);
    };

    return (
      <>
        {isLoading || finalColumnDefs === null ? (
          <LinearProgress />
        ) : (
          <Box
            className="ag-theme-quartz"
            sx={{
              paddingX: theme.spacing(2),
              paddingBottom: theme.spacing(1),
              flex: 1,
            }}
          >
            <Box
              className={`ag-theme-quartz ${className} ${cellHeight && cellHeight !== "Short" ? "cell-wrap" : ""}`}
              style={{ height: "100%" }}
            >
              <AgGridReact
                ref={gridApiRef}
                columnDefs={finalColumnDefs}
                getRowHeight={(params) => {
                  if (params?.node?.rowPinned === "bottom") return 30;
                  return (
                    userTraceRowHeightMapping[cellHeight]?.height ??
                    userTraceRowHeightMapping.Short.height
                  );
                }}
                rowHeight={
                  userTraceRowHeightMapping[cellHeight]?.height ??
                  userTraceRowHeightMapping.Short.height
                }
                statusBar={statusBar}
                rowSelection={{ mode: "multiRow" }}
                className="clean-data-table"
                theme={agTheme}
                rowModelType="serverSide"
                serverSideDatasource={dataSource}
                pagination={false}
                cacheBlockSize={DATASET_ROWS_LIMIT}
                maxBlocksInCache={5}
                rowBuffer={10}
                suppressServerSideFullWidthLoadingRow={true}
                serverSideInitialRowCount={DATASET_ROWS_LIMIT}
                defaultColDef={defaultColDef}
                suppressRowClickSelection={true}
                rowStyle={{ cursor: "pointer" }}
                onRowClicked={onRowClicked}
                onColumnMoved={onColumnMoved}
                onSelectionChanged={onSelectionChanged}
                getRowId={({ data }) => data.session_id}
                onFirstDataRendered={({ api }) => {
                  api.setServerSideSelectionState({
                    selectAll: selectAll,
                    toggledNodes: toggledNodes,
                  });
                }}
                onModelUpdated={({ api }) => {
                  if (!selectAll && !toggledNodes?.length) {
                    api.deselectAll();
                    return;
                  }
                }}
                onGridReady={onGridReady}
              />
            </Box>
            {currentRowData ? (
              <TracesDrawer
                open={open}
                onClose={handleDrawerClose}
                rowData={currentRowData}
              />
            ) : null}
          </Box>
        )}
      </>
    );
  },
);

SessionGrid.displayName = "SessionGrid";

SessionGrid.propTypes = {
  updateObj: PropTypes.objectOf(PropTypes.bool).isRequired,
  columns: PropTypes.array,
  setColumns: PropTypes.func,
  filters: PropTypes.array,
  onGridReady: PropTypes.func,
  projectId: PropTypes.string,
  cellHeight: PropTypes.string,
  onSelectionChanged: PropTypes.func,
  className: PropTypes.string,
  pendingCustomColumnsRef: PropTypes.object,
  isOnSavedView: PropTypes.bool,
};

export default SessionGrid;
