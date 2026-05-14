import React, { useCallback, useEffect, useMemo, useRef } from "react";
import { Box, useTheme, Typography } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import "src/styles/clean-data-table.css";
import useUsersStore from "./Store/usersStore";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { getUsersColumnConfig, userTraceRowHeightMapping } from "./common";
import { mergeCellStyle } from "../LLMTracing/common";
import axios, { endpoints } from "src/utils/axios";
import { objectCamelToSnake } from "src/utils/utils";
import { canonicalizeApiFilterColumnIds } from "src/utils/filter-column-ids";
import { useNavigate, useParams } from "react-router";
import { useDebounce } from "src/hooks/use-debounce";
import { useGetValidatedFilters } from "src/hooks/use-get-validated-filters";
import PropTypes from "prop-types";
import { getRandomId } from "src/utils/utils";
import NoRowsOverlay from "src/sections/project-detail/CompareDrawer/NoRowsOverlay";
import { APP_CONSTANTS } from "src/utils/constants";

const USERS_GRID_THEME_PARAMS = {
  columnBorder: false,
  headerColumnBorder: { width: 0 },
  wrapperBorder: { width: 0 },
  wrapperBorderRadius: 0,
  rowBorder: { width: 1, color: "rgba(0,0,0,0.06)" },
  headerFontSize: "13px",
  headerFontWeight: 500,
  headerBackgroundColor: "transparent",
  rowHoverColor: "rgba(120,87,252,0.04)",
};

const UsersGrid = React.memo(
  ({
    hasActiveFilter,
    setHasData,
    setIsLoading,
    setSearchState,
    cellHeight,
  }) => {
    const theme = useTheme();
    const agTheme = useAgThemeWith(USERS_GRID_THEME_PARAMS);
    const gridApiRef = useRef(null);
    const {
      setGridApi,
      searchQuery,
      selectedAll,
      selectedRowsData,
      setSelectedAll,
      setSelectedRowsData,
      clearSelection,
      columns,
      setColumns,
      filters,
      selectedProjectDay,
      selectedProjectId,
    } = useUsersStore();

    const convertToISO = (dateArray) => {
      return dateArray.map((date) => new Date(date).toISOString());
    };
    const userFirstRef = useRef(true);

    const today = new Date();
    const pastDate = new Date();
    pastDate.setDate(today.getDate() - selectedProjectDay);

    const { observeId } = useParams();
    const updatedObserveId = selectedProjectId || observeId;
    const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

    const projectFilterId = useMemo(() => getRandomId(), []);

    const projectFilter = useMemo(
      () => ({
        columnId: "created_at",
        filterConfig: {
          filterType: "datetime",
          filterOp: "between",
          filterValue: convertToISO([pastDate, today]),
        },
        id: projectFilterId,
        _meta: {
          parentProperty: "",
        },
      }),
      [projectFilterId, selectedProjectDay],
    );

    const validatedUserFilters = useGetValidatedFilters(filters);

    const validatedFilters = useMemo(() => {
      return [...validatedUserFilters, projectFilter];
    }, [validatedUserFilters, projectFilter]);

    const navigate = useNavigate();

    const hasProjectFilter =
      selectedProjectId || (selectedProjectDay !== 90 && !selectedProjectId);

    useEffect(() => {
      const initial = getUsersColumnConfig();

      const transformed = initial.map((col) => ({
        id: col.field,
        name: col.headerName || "",
        isVisible: col.hide === undefined ? true : !col.hide,
        groupBy: null,
        outputType: null,
      }));

      setColumns(transformed);
    }, []);

    const userColumnDefs = useMemo(() => {
      const baseConfig = getUsersColumnConfig();

      // If columns from store isn't ready, use baseConfig directly
      if (!columns || !Array.isArray(columns)) {
        return baseConfig.map((col) => ({
          ...col,
          colId: col.field,
          hide: col.hide || false,
          lockVisible: false,
          minWidth: col?.minWidth ?? 120,
        }));
      }

      const buildColDef = (col) => {
        const originalCol = baseConfig.find((c) => c.field === col.id);

        // Custom (attribute-based) columns have no entry in baseConfig —
        // build a fallback col def that reads `data[col.id]`. Values for
        // these keys are not yet returned by getUsersList; see
        // plans/delegated-marinating-flame.md "Known limitation".
        if (!originalCol && col.groupBy === "Custom Columns") {
          return {
            headerName: col.name || col.id,
            field: col.id,
            colId: col.id,
            hide: !col.isVisible,
            lockVisible: false,
            minWidth: 160,
            flex: 1,
            valueGetter: (params) => params.data?.[col.id] ?? null,
            valueFormatter: (params) =>
              params.value === null || params.value === undefined
                ? "—"
                : String(params.value),
          };
        }

        return {
          ...originalCol,
          colId: col.id,
          hide: !col.isVisible,
          lockVisible: false,
          minWidth: originalCol?.minWidth ?? 120,
        };
      };

      const customCols = columns.filter((c) => c?.groupBy === "Custom Columns");
      const otherCols = columns.filter((c) => c?.groupBy !== "Custom Columns");

      const result = otherCols.map(buildColDef);

      // Group custom columns under a "Custom Columns" header (TH-4151)
      if (customCols.length > 0) {
        result.push({
          headerName: "Custom Columns",
          children: customCols.map((c) => {
            const colDef = buildColDef(c);
            return {
              ...colDef,
              minWidth: 200,
              flex: 1,
              cellStyle: mergeCellStyle(colDef, { paddingInline: 0 }),
            };
          }),
        });
      }

      return result;
    }, [columns]);

    const dataSource = useMemo(
      () => ({
        getRows: async (params) => {
          try {
            setIsLoading(true);
            params.api.hideOverlay();
            const { request } = params;
            const pageSize = request.endRow - request.startRow;
            const pageNumber = Math.floor(request.startRow / pageSize);
            if (userFirstRef.current) {
              const savedSort = localStorage.getItem(
                `ag-grid-sort-model-${updatedObserveId}`,
              );
              if (savedSort) {
                const sortModel = JSON.parse(savedSort);
                params.api.applyColumnState({
                  state: sortModel.map((sort) => ({
                    colId: sort.colId,
                    sort: sort.sort,
                  })),
                  defaultState: { sort: null },
                });
              }
              userFirstRef.current = false;
            }
            const results = await axios.get(endpoints.project.getUsersList(), {
              params: {
                // Omit project_id when there's no project context — the
                // backend handles project_id=null as org-scoped, used by
                // the cross-project users page at /dashboard/users.
                ...(updatedObserveId ? { project_id: updatedObserveId } : {}),
                sort_params:
                  request.sortModel && request.sortModel.length > 0
                    ? JSON.stringify({
                        column_id: request.sortModel[0].colId,
                        direction: request.sortModel[0].sort,
                      })
                    : JSON.stringify(request.sortModel),
                search: debouncedSearchQuery?.length
                  ? debouncedSearchQuery
                  : null,
                page_size: pageSize,
                current_page_index: pageNumber,
                filters: JSON.stringify(
                  canonicalizeApiFilterColumnIds(
                    objectCamelToSnake(validatedFilters),
                  ),
                ),
              },
            });

            const res = results?.data?.result;
            const userData = res?.table || [];
            const hasResults = userData.length > 0;
            setHasData(hasResults);
            const total = res?.total_count ?? 0;

            if (!hasResults) {
              params.api.showNoRowsOverlay();
            } else {
              params.api.hideOverlay();
            }

            if (debouncedSearchQuery === "") {
              if (hasActiveFilter || hasProjectFilter) {
                setSearchState("searching");
              } else {
                setSearchState(hasResults ? "idle" : "empty");
              }
            } else {
              setSearchState("searching");
            }

            // Merge new total into AG Grid's context
            const existingContext = params.api.getGridOption("context") || {};
            params.api.setGridOption("context", {
              ...existingContext,
              totalRowCount: total,
            });

            // Clear selection when no data
            if (total === 0) {
              clearSelection();
            }

            params.success({
              rowData: userData,
              rowCount: total,
            });
          } catch (error) {
            // Clear selection on error
            clearSelection();
            // setHasData(false);
            if (debouncedSearchQuery === "") {
              setSearchState("empty");
            }
            // Pass empty data on error instead of calling params.fail()
            params.success({
              rowData: [],
              rowCount: 0,
            });
            params.api.showNoRowsOverlay();
          } finally {
            setIsLoading(false);
          }
        },
        getRowId: ({ data }) => data.user_id,
      }),
      [
        updatedObserveId,
        debouncedSearchQuery,
        validatedFilters,
        clearSelection,
        setHasData,
        setIsLoading,
        setSearchState,
        hasActiveFilter,
        hasProjectFilter,
      ],
    );

    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        filter: false,
        resizable: true,
        suppressHeaderMenuButton: true,
        suppressHeaderContextMenu: true,
        cellStyle: {
          padding: "0px 20px",
          fontSize: "14px",
          height: "100%",
          display: "flex",
          alignItems: "center",
        },
      }),
      [],
    );

    const onCellClicked = useCallback(
      (event) => {
        const colId = event?.colDef?.colId;
        if (colId === "actions") return;
        if (colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN) {
          const selected = event.node.isSelected();
          event.node.setSelected(!selected);
          return;
        }

        const userId = event.data?.user_id;
        if (!userId) return;

        // All user-detail navigation goes through the cross-project page.
        // It accepts a single user id and shows that user's traces +
        // sessions across every project in the org.
        navigate(`/dashboard/users/${encodeURIComponent(userId)}`);
      },
      [navigate],
    );

    const onColumnHeaderClicked = useCallback(
      (event) => {
        if (event.column.colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN)
          return;

        const api = event.api;
        if (selectedAll) {
          api.deselectAll();
          clearSelection();
        } else {
          api.selectAll();
          setSelectedAll(true);
        }
      },
      [selectedAll, setSelectedAll, clearSelection],
    );

    const onSelectionChanged = useCallback(() => {
      if (!gridApiRef.current) return;

      const api = gridApiRef.current.api;
      const selectedNodes = api.getSelectedNodes();
      const selectedData = selectedNodes.map((node) => node.data);

      const total = api.getGridOption("context")?.totalRowCount || 0;
      setSelectedRowsData(selectedData);
      setSelectedAll(selectedData.length === total && total > 0);
    }, [setSelectedAll, setSelectedRowsData]);

    const onGridReady = useCallback(
      (params) => {
        gridApiRef.current = params;
        setGridApi(params.api); // Store the grid API reference

        // Initial sync of selection state
        if (selectedRowsData.length > 0) {
          params.api.forEachNode((node) => {
            const isSelected = selectedRowsData.some(
              (row) => row.id === node.data.id,
            );
            node.setSelected(isSelected);
          });
        }
      },
      [selectedRowsData, setGridApi],
    );

    const containerStyle = useMemo(
      () => ({
        flexGrow: 1,
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }),
      [],
    );

    const gridWrapperStyle = useMemo(
      () => ({
        paddingBottom: theme.spacing(1),
        flex: 1,
        width: "100%",
        overflow: "auto",
        minWidth: 0,
      }),
      [theme],
    );
    const fullHeightStyle = useMemo(
      () => ({
        height: "100%",
        "& .ag-cell:not([col-id='ag-Grid-SelectionColumn'])": {
          display: "flex",
          alignItems: "center",
          padding: 0,
        },
        "& .ag-cell:not([col-id='ag-Grid-SelectionColumn']) .ag-cell-wrapper": {
          display: "flex",
          alignItems: "center",
          height: "100%",
          width: "100%",
          flex: 1,
        },
        "& .ag-cell[col-id='ag-Grid-SelectionColumn']": {
          display: "flex",
          alignItems: "center",
        },
      }),
      [],
    );
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

    const onSortChanged = (params) => {
      const sortModel = params.api
        .getColumnState()
        .filter((col) => col.sort != null)
        .map((col) => ({
          colId: col.colId,
          sort: col.sort,
        }));

      if (sortModel.length > 0) {
        localStorage.setItem(
          `ag-grid-sort-model-${updatedObserveId}`,
          JSON.stringify(sortModel),
        );
      } else {
        localStorage.removeItem(`ag-grid-sort-model-${updatedObserveId}`);
      }
    };
    return (
      <Box sx={containerStyle}>
        <Box
          className={`ag-theme-quartz ${cellHeight && cellHeight !== "Short" ? "cell-wrap" : ""}`}
          sx={gridWrapperStyle}
        >
          <Box className="ag-theme-quartz" sx={fullHeightStyle}>
            <AgGridReact
              className="clean-data-table"
              ref={(params) => {
                gridApiRef.current = params;
              }}
              onSortChanged={onSortChanged}
              onColumnMoved={onColumnMoved}
              columnDefs={userColumnDefs}
              serverSideDatasource={dataSource}
              headerHeight={40}
              rowHeight={userTraceRowHeightMapping[cellHeight]?.height ?? 40}
              theme={agTheme}
              rowSelection={{ mode: "multiRow" }}
              pagination={true}
              paginationPageSize={10}
              rowModelType="serverSide"
              paginationPageSizeSelector={false}
              defaultColDef={defaultColDef}
              onColumnHeaderClicked={onColumnHeaderClicked}
              suppressRowClickSelection={true}
              rowStyle={{ cursor: "pointer" }}
              suppressSizeToFit={true}
              suppressAutoSize={true}
              suppressServerSideFullWidthLoadingRow={true}
              serverSideInitialRowCount={5}
              animateRows={true}
              getMainMenuItems={(params) =>
                params.defaultItems.filter((item) => item !== "columnChooser")
              }
              onCellClicked={onCellClicked}
              onRowSelected={onSelectionChanged}
              onGridReady={onGridReady}
              noRowsOverlayComponent={() =>
                NoRowsOverlay(
                  <Typography
                    typography="m3"
                    color="text.primary"
                    fontWeight="fontWeightMedium"
                  >
                    No active users for current filters
                  </Typography>,
                )
              }
            />
          </Box>
        </Box>
      </Box>
    );
  },
);

UsersGrid.displayName = "UsersGrid";

UsersGrid.propTypes = {
  hasActiveFilter: PropTypes.bool,
  setHasData: PropTypes.func,
  setIsLoading: PropTypes.func,
  setSearchState: PropTypes.func,
  cellHeight: PropTypes.string,
};

export default UsersGrid;
