import { Box, Stack, Typography } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { setMenuIcons } from "src/utils/MenuIconSet/setMeniIcons";
import { menuIcons } from "src/utils/MenuIconSet/svgIcons";
import { useLocalStorage } from "src/hooks/use-local-storage";
import { preventHeaderSelection, resetDLabels } from "src/utils/utils";
// import "./ExperimentDataViewStyle.css";
import { ExperimentDataDefaultColDef } from "src/sections/experiment-detail/ExperimentData/tableConfig";
import CustomExperimentGroupHeader from "src/sections/experiment-detail/ExperimentData/CustomExperimentGroupHeader";
import ExperimentStatusBar from "src/sections/experiment-detail/ExperimentData/ExperimentStatusBar";
import { getEachCompareColumnDef } from "./compareTableConfig";
import { typography } from "src/theme/typography";
import CompareDatasetDetailDrawer from "./CompareDatasetDetailDrawer/CompareDatasetDetailDrawer";
import { useDeleteCompare } from "src/api/develop/dataset-compare";
import { useDevelopDetailContext } from "src/pages/dashboard/Develop/Context/DevelopDetailContext";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import { APP_CONSTANTS } from "src/utils/constants";
import CompositeEvalDialog from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/CompositeEvalDialog";

// Define status types that require refreshing
const RefreshStatus = [
  "Running",
  "NotStarted",
  "Editing",
  "ExperimentEvaluation",
  "PartialRun",
];

const CompareData = ({
  gridApiRef,
  baseColumn,
  selectedDatasetsValues,
  setColumns,
  setCommonColumn,
  setDatasetInfo,
  columns,
  selectedColumns,
  setCompareColumnDefs,
  compareId,
}) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.dataGridPadded);
  const { state: showSummary, update: setShowSummary } = useLocalStorage(
    "showSummaryExp",
    { vals: [] },
  );
  const [columnDefs, setColumnDefs] = useState([
    {
      headerName: "Column 1",
      field: "name",
      flex: 1,
    },
    {
      headerName: "Column 2",
      field: "numberOfDatapoints",
      flex: 1,
    },
    {
      headerName: "Column 3",
      field: "numberOfExperiments",
      flex: 1,
    },
    {
      headerName: "Column 4",
      field: "numberOfOptimisations",
      flex: 1,
    },
  ]);
  const [columnConfig, setColumnConfig] = useState([]);
  const [columnData, setColumnData] = useState([]);
  const [hasData, setHasData] = useState(true);
  const isRefreshingColumns = useRef(null);

  preventHeaderSelection();
  const [expandRow, setExpandRow] = useState(null);
  const { diffMode } = useDevelopDetailContext();
  const diffModeRef = useRef(diffMode);

  const { mutate: deleteCompare } = useDeleteCompare();

  React.useEffect(() => {
    diffModeRef.current = diffMode;
    gridApiRef?.current?.api?.refreshCells({ force: true });
  }, [diffMode, gridApiRef]);

  React.useEffect(() => {
    if (gridApiRef.current?.api && !hasData) {
      gridApiRef.current.api.showNoRowsOverlay();
    }
  }, [gridApiRef, hasData]);

  React.useEffect(() => {
    setColumnDefs(filterColumnDef(columnData, selectedColumns));
  }, [columnData, selectedColumns]);

  const refreshGrid = useCallback(() => {
    gridApiRef.current?.api?.refreshServerSide();
  }, [gridApiRef]);

  // Update the interval effect to only run when there are pending items
  React.useEffect(() => {
    let intervalId;

    // Only create the interval if there are pending columns
    if (isRefreshingColumns.current) {
      intervalId = setInterval(() => {
        refreshGrid();
      }, 10000);
    }

    // Clean up function to clear the interval when component unmounts or dependencies change
    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [isRefreshingColumns, refreshGrid]); // Dependency on pending items

  // Add effect to refresh grid when selectedDatasetsValues changes
  React.useEffect(() => {
    if (compareId?.current) {
      deleteCompare(compareId.current);
      compareId.current = null;
    }
    if (gridApiRef.current && gridApiRef.current.api) {
      gridApiRef.current.api.refreshServerSide({ route: [] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDatasetsValues]);

  function filterColumnDef(columnDefs, selectedColumns) {
    if (columnDefs?.length < 1) return;

    return columnDefs.map((col) => {
      if (!selectedColumns?.includes(col?.id)) {
        // Handle columns with children
        if (col?.children && col?.children?.length > 0) {
          return {
            ...col,
            children: col.children.map((childCol) => ({
              ...childCol,
              hide: !selectedColumns.includes(childCol?.id),
            })),
          };
        } else {
          return {
            ...col,
            hide: true,
          };
        }
      } else {
        // Handle columns with children
        if (col?.children && col?.children?.length > 0) {
          return {
            ...col,
            children: col.children.map((childCol) => ({
              ...childCol,
              hide: false, // Show the child column
            })),
          };
        } else {
          return {
            ...col,
            hide: false, // Show the column itself
          };
        }
      }
    });
  }

  const menuList = (params) => {
    // const isGroupColumn = params.column && params.column.getColDef().children;
    const allMenuItems = setMenuIcons(params);
    const menuItems = allMenuItems;
    const column = params.column.colDef.col;
    const extraMenuItems = [];
    if (
      column.originType === "evaluation" &&
      !column.name.includes("-reason")
    ) {
      const isSummaryShown = showSummary.vals.find((v) => {
        return v.id === column.id;
      });
      extraMenuItems.push({
        name: isSummaryShown ? "Hide Summary" : "Show Summary",
        action: () => {
          if (isSummaryShown) {
            setShowSummary(
              "vals",
              showSummary.vals.filter((v) => v.id !== column.id),
            );
          } else {
            setShowSummary("vals", [...showSummary.vals, column]);
          }
        },
        icon: menuIcons["Show Summary"],
      });
      extraMenuItems.push("separator");
    }
    return [...extraMenuItems, ...menuItems];
  };

  const postProcessPopup = useCallback((params) => {
    if (params.type !== "columnMenu") {
      return;
    }
    const ePopup = params.ePopup;
    ePopup.style.backgroundColor = "var(--bg-paper, #fff)";
    ePopup.style.borderRadius = "12px";
    ePopup.style.border = "1px solid var(--border-default, #e5e7eb)";
    ePopup.style.fontFamily = "Inter, sans-serif";
    ePopup.style.color = "var(--text-primary)";

    const menuItems = ePopup.querySelectorAll(".ag-menu-option");
    menuItems.forEach((item) => {
      item.style.borderRadius = "4px";
      item.style.transition = "background-color 0.15s";
      item.addEventListener("mouseenter", () => {
        item.style.backgroundColor =
          "var(--surface-row-hover, rgba(0,0,0,0.04))";
      });
      item.addEventListener("mouseleave", () => {
        item.style.backgroundColor = "transparent";
      });
    });

    const elements = ePopup.querySelectorAll('span[data-ref="eName"]');
    elements.forEach((element) => {
      if (
        element.textContent.trim() === "Show Summary" ||
        element.textContent.trim() === "Hide Summary"
      ) {
        element.style.color = "var(--primary-main)";
      }
      element.style.fontWeight = 400;
    });
  }, []);

  const gridOptions = {
    tooltipShowDelay: 0,
    enableBrowserTooltips: false,
    getRowStyle: (params) => {
      if (params.node.rowPinned === "bottom") {
        return { height: "30px !important" }; // Adjust height as needed
      }
      return null;
    },
  };

  const [pinnedBottomRowData, setPinnedBottomRowData] = useState([]);
  const [columnAvgs, setColumnAvgs] = useState([]);

  const addHeaderAndIndexToColumnMap = (mapping) => {
    return mapping.map((col) => {
      if (col?.children?.length > 0) {
        for (let i = 0; i < col.children.length; i++) {
          col.children[i].headerComponentParams = {
            ...col.children[i].headerComponentParams,
            head: String.fromCharCode(65 + i),
            index: i,
          };
        }
      }
      return col;
    });
  };

  useEffect(() => {
    setColumnData(columns);
    setColumnDefs(addHeaderAndIndexToColumnMap(columns));
  }, [columns]);

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;

        // Check if selected datasets is valid
        if (!selectedDatasetsValues || selectedDatasetsValues.length <= 1) {
          setHasData(false);
          params.success({
            rowData: [],
            rowCount: 0,
          });
          return;
        }

        // request has startRow and endRow get next page number and each page has 10 rows
        const selectedDatasets = selectedDatasetsValues.slice(1);
        const pageNumber = Math.floor(request.startRow / 10);

        try {
          const { data } = await axios.post(
            endpoints.dataset.getCompareDataset(selectedDatasetsValues[0]),
            {
              dataset_ids: selectedDatasets,
              base_column_name: baseColumn,
              page_size: 10,
              current_page_index: pageNumber,
              compare_id: compareId?.current,
            },
          );

          compareId.current = data?.result?.metadata?.compareId;

          // Check if data is empty or doesn't have required structure
          if (!data?.result?.table || data.result.table.length === 0) {
            setHasData(false);
            params.success({
              rowData: [],
              rowCount: 0,
            });
            return;
          }

          resetDLabels();
          setHasData(true);
          setCommonColumn(data?.result?.metadata?.commonColumnNames);
          setDatasetInfo(data?.result?.metadata?.datasetInfo);
          const columns = data?.result?.columnConfig;

          setColumnConfig(columns);

          const commonHeaderClass = "custom-header";

          const columnMap = [];

          const grouping = {};

          // Track columns that need refreshing
          const refresh = [];

          for (const eachCol of columns) {
            // Check if column has a pending status
            if (RefreshStatus.includes(eachCol?.status)) {
              refresh.push(eachCol.id);
            }

            if (eachCol?.group?.id) {
              if (!grouping[eachCol?.group?.id]) {
                grouping[eachCol?.group?.id] = [];
              }
              grouping[eachCol?.group?.id].push(
                getEachCompareColumnDef(eachCol, diffModeRef),
              );
            }
          }

          // Update refreshing state if needed
          if (refresh.length > 0) {
            if (!isRefreshingColumns.current) {
              isRefreshingColumns.current = refresh;
            }
          } else {
            isRefreshingColumns.current = null;
          }

          Object.entries(grouping).forEach(([_, value]) => {
            if (value.length === 1) {
              columnMap.push({ ...value[0], id: value?.[0]?.field });
            } else {
              const children = [];
              for (const eachCol of value) {
                const headerName = eachCol?.headerName || eachCol?.col?.name;
                const isChildColumn = true;
                if (eachCol?.col?.originType === "evaluation") {
                  const findInShowSummary = showSummary?.vals?.find(
                    (v) =>
                      v.group.id === eachCol?.col?.group?.id &&
                      v.name === eachCol?.col?.name?.replace("-reason", ""),
                  );
                  if (
                    eachCol?.col?.name?.includes("-reason") &&
                    !(
                      findInShowSummary &&
                      eachCol?.col?.name?.replace("-reason", "") ===
                        findInShowSummary.name
                    )
                  ) {
                    continue;
                  }

                  children.push({
                    ...eachCol,
                    id: `${value?.[0]?.field}-${eachCol?.headerName}`,
                    headerTooltip: eachCol?.headerTooltip || headerName,
                    minWidth: 300,
                    headerComponentParams: {
                      ...eachCol.headerComponentParams,
                      isGrouped: true,
                    },
                    headerClass: isChildColumn ? "child-column-header" : "",
                  });
                } else {
                  children.push({
                    ...eachCol,
                    id: `${value?.[0]?.field}-${eachCol?.headerName}`,
                    headerTooltip: eachCol?.headerTooltip || headerName,
                    headerComponentParams: {
                      ...eachCol.headerComponentParams,
                      isGrouped: true,
                    },
                    headerClass: isChildColumn ? "child-column-header" : "",
                  });
                }
              }
              if (children?.length > 0) {
                columnMap?.push({
                  field: children[0]?.col?.group?.id,
                  headerName: children[0]?.col?.group?.name,
                  id: children[0]?.col?.group?.id,
                  headerGroupComponent: CustomExperimentGroupHeader,
                  marryChildren: true,
                  children,
                  minWidth: 300,
                  headerGroupComponentParams: {
                    group: children[0]?.col?.group,
                  },
                  headerTooltip: children[0]?.col?.name,
                  headerClass: commonHeaderClass,
                });
              }
            }
          });

          const tempArr = [];
          for (let i = 0; i < columnMap?.length; i++) {
            tempArr.push(columnMap[i]?.headerName);
          }

          const columnWithHeadAndIndex =
            addHeaderAndIndexToColumnMap(columnMap);

          setColumns(columnMap);
          setColumnData(columnMap);
          setColumnDefs(columnWithHeadAndIndex);
          setCompareColumnDefs(columnMap);
          const rows = data?.result?.table;

          params.success({
            rowData: rows,
            rowCount: data?.result?.metadata.total_rows,
          });

          const filteredColumns = columnMap.flatMap((col) =>
            col.children
              ? col.children.filter(
                  (child) =>
                    child?.col?.averageScore !== null &&
                    child?.col?.averageScore !== undefined,
                )
              : col?.col?.averageScore !== null &&
                  col?.col?.averageScore !== undefined
                ? [col]
                : [],
          );

          const pinnedRow = {};
          filteredColumns.forEach((col) => {
            if (col.field) {
              const avgValue = `${col.col.averageScore.toFixed(2)}%`;
              pinnedRow[col.field] = `Average: ${avgValue}`;
            }
          });

          setPinnedBottomRowData([pinnedRow]);

          const averages = Object.entries(
            data?.result?.metadata?.averageScore || {},
          ).map(([id, value]) => ({
            id,
            value,
          }));

          setColumnAvgs(averages);
        } catch (error) {
          setHasData(false);
          isRefreshingColumns.current = null;
          params.success({
            rowData: [],
            rowCount: 0,
          });
        }
      },
      getRowId: (data) => data.rowId,
    }),
    [
      selectedDatasetsValues,
      baseColumn,
      compareId,
      setCommonColumn,
      setDatasetInfo,
      setColumns,
      setCompareColumnDefs,
      showSummary?.vals,
    ],
  );

  const statusBar = {
    statusPanels: [
      {
        statusPanel: ExperimentStatusBar,
        align: "left",
        statusPanelParams: { columnAvgs },
      },
    ],
  };

  // Function to render the overlay message based on state
  const NoRowsOverlay = () => {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          minHeight: "400px",
        }}
      >
        <img
          style={{
            height: "109px",
            width: "117px",
          }}
          alt="no dataset added ui"
          src="/assets/illustrations/no-dataset-added.svg"
        />
        <Stack direction={"column"} rowGap={0} alignItems={"center"}>
          <Typography
            sx={{
              mt: 2.5,
              mb: 2,
              fontFamily: typography.fontFamily,
              color: "text.primary",
              ...typography.subtitle2,
            }}
          >
            No Common rows
          </Typography>
          <Typography
            sx={{
              fontFamily: typography.fontFamily,
              color: "text.disabled",
              fontWeight: typography.fontWeightRegular,
              ...typography.s1,
            }}
          >
            There are no common rows to show
          </Typography>
        </Stack>
      </Box>
    );
  };

  return (
    <AudioPlaybackProvider>
      <Box
        className="ag-theme-quartz"
        sx={{
          flex: 1,
          padding: "12px",
          height: "100%",
          overflowY: "hidden",
          position: "relative",
          "& .ag-body-horizontal-scroll": {
            position: "absolute !important",
            bottom: "0 !important",
            left: "0 !important",
            right: "0 !important",
            zIndex: 1, // Ensure it's above other content
          },
        }}
      >
        {/* <ExperimentDetailDrawer
                open={Boolean(expandRow)}
                onClose={() => setExpandRow(null)}
                row={expandRow}
                columnConfig={columnConfig}
            />  */}
        {/* {experimentMeta ? (
                <RunEvaluation
                    open={evaluateOpen}
                    onClose={() => setEvaluateOpen(false)}
                    allColumns={allColumnOptions}
                    refreshGrid={refreshGrid}
                    datasetId={experimentMeta?.dataset}
                    experimentEval={{
                        experimentId,
                        baseColumnId: experimentMeta?.column,
                    }}
                />
            ) : (
                <></>
            )} */}
        <Box
          className="experiment-view-grid"
          sx={{
            height: "100%",
            position: "relative",
          }}
        >
          <CompositeEvalDialog />
          <AgGridReact
            ref={gridApiRef}
            columnDefs={columnDefs}
            noRowsOverlayComponent={NoRowsOverlay}
            onColumnHeaderClicked={(event) => {
              if (
                event.column.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
              ) {
                // Get all displayed rows
                const displayedNodes = [];
                event.api.forEachNode((node) => {
                  if (node.displayed) {
                    displayedNodes.push(node);
                  }
                });

                // Check if all displayed rows are selected
                const allSelected = displayedNodes.every((node) =>
                  node.isSelected(),
                );

                // Toggle selection based on current state
                if (allSelected) {
                  event.api.deselectAll();
                } else {
                  event.api.selectAll();
                }
              }
            }}
            defaultColDef={ExperimentDataDefaultColDef}
            paginationAutoPageSize={true}
            suppressRowClickSelection={true}
            suppressServerSideFullWidthLoadingRow={true}
            serverSideInitialRowCount={5}
            paginationPageSizeSelector={false}
            rowModelType="serverSide"
            serverSideDatasource={dataSource}
            pagination={false}
            cacheBlockSize={10}
            statusBar={statusBar}
            pinnedBottomRowData={pinnedBottomRowData}
            gridOptions={gridOptions}
            getMainMenuItems={menuList}
            postProcessPopup={postProcessPopup}
            suppressColumnMoveAnimation={true}
            suppressColumnVirtualisation={true}
            theme={agTheme}
            getRowHeight={(params) =>
              params.node.rowPinned === "bottom" ? 40 : 120
            }
            onRowClicked={(params) => {
              if (window.__audioClick) {
                window.__audioClick = false;
                return;
              }

              const target = params.event?.target;
              if (
                target?.closest(".audio-control-btn") ||
                target?.closest(".wrapper")
              ) {
                return;
              }

              if (
                params.event.target.closest(
                  ".ag-cell[col-id=APP_CONSTANTS.AG_GRID_SELECTION_COLUMN]",
                )
              ) {
                const selected = params.node.isSelected();
                params.node.setSelected(!selected);
                return;
              }
              if (params?.rowPinned !== "bottom") {
                setExpandRow({
                  ...params?.data,
                  index: params.rowIndex,
                });
              }
            }}
          />
        </Box>
      </Box>
      <CompareDatasetDetailDrawer
        open={Boolean(expandRow)}
        onClose={() => setExpandRow(null)}
        row={expandRow}
        columnConfig={columnConfig}
        compareId={compareId?.current}
      />
    </AudioPlaybackProvider>
  );
};

CompareData.propTypes = {
  gridApiRef: PropTypes.any,
  selectedColumns: PropTypes.array,
  columns: PropTypes.array,
  setDatasetInfo: PropTypes.func,
  setCommonColumn: PropTypes.func,
  setColumns: PropTypes.func,
  baseColumn: PropTypes.string,
  selectedDatasetsValues: PropTypes.array,
  setCompareColumnDefs: PropTypes.func,
  diffMode: PropTypes.bool,
  compareId: PropTypes.any,
};

export default CompareData;
