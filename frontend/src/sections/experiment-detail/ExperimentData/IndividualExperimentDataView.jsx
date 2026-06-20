import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { useMutation } from "@tanstack/react-query";
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
import { useParams } from "react-router";

import {
  ExperimentDataDefaultColDef,
  getIndividualExperimentColumnConfig,
} from "./tableConfig";
import ExperimentDetailDrawer from "./ExperimentDetailDrawer";
import { useExperimentDetailContext } from "../experiment-context";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import EvaluationDrawer from "src/sections/common/EvaluationDrawer/EvaluationDrawer";
import { APP_CONSTANTS } from "src/utils/constants";

const RefreshStatus = [
  "Running",
  "NotStarted",
  "Editing",
  "ExperimentEvaluation",
];

const IndividualExperimentDataView = () => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.withColumnBorder);
  const { dataset } = useParams();
  const { individualExperimentId } = useParams();
  const gridApiRef = useRef(null);
  const [columnDefs, setColumnDefs] = useState([]);
  const [expandRow, setExpandRow] = useState(null);
  const [columnConfig] = useState([]);
  const [pinnedBottomRowData, setPinnedBottomRowData] = useState([]);
  const [isRefreshing, setIsRefreshing] = useState(null);
  const [allRows, setAllRows] = useState([]);
  const [totalRows] = useState(0);

  const { setEvaluateOpen, evaluateOpen } = useExperimentDetailContext();
  const [experimentMeta] = useState(null);

  const isHoverButtonVisible = false;
  useEffect(() => {
    const interval = setInterval(() => {
      if (isRefreshing) {
        gridApiRef?.current?.api?.refreshServerSide();
      }
    }, 10000);
    return () => clearInterval(interval);
  }, [isRefreshing]);

  const storeAllDisplayedRows = () => {
    const newRows = [];

    gridApiRef.current?.api.forEachNode((node) => {
      if (node.displayed) {
        newRows.push(node);
      }
    });

    setAllRows((prev) => {
      const mergedMap = new Map();

      // Use unique row ID instead of rowIndex
      prev.forEach((row) => {
        const id = row.data?.rowId;
        if (id !== undefined) {
          mergedMap.set(id, row);
        }
      });

      newRows.forEach((row) => {
        const id = row.data?.rowId;
        if (id !== undefined) {
          mergedMap.set(id, row);
        }
      });

      return Array.from(mergedMap.values());
    });
  };

  const { mutate: updateDataset } = useMutation({
    mutationFn: (d) => axios.put(endpoints.develop.updateDataset(dataset), d),
  });

  const { data: datasetList } = useDevelopDatasetList();

  const currentDataset = datasetList?.find((v) => v.datasetId === dataset);

  const allColumnOptions = useMemo(() => {
    return columnConfig.reduce((acc, curr) => {
      if (
        curr?.originType !== "evaluation" &&
        !curr?.name?.startsWith("Experiment")
      ) {
        acc.push({
          headerName: curr.name,
          field: curr.id,
        });
      }
      return acc;
    }, []);
  }, [columnConfig]);
  const getMainMenuItems = (params) => {
    const menuItems = params.defaultItems.slice(0);
    const column = params.column.colDef.col;
    const extraMenuItems = [];
    if (
      ["run_prompt", "evaluation", "optimization"].includes(column?.originType)
    ) {
      extraMenuItems.push({
        name: "Configure",
        action: () => {
          // if (column.originType === "run_prompt")
          //   setConfigureRunPrompt(column);
          // else if (column.originType === "evaluation")
          //   setConfigureEval({ id: column?.sourceId, evalType: "user" });
        },
      });
    }
    // extraMenuItems.push({
    //   name: "Edit Column",
    //   action: (v) => {
    //     setEditColumn(column);
    //   },
    // });
    // extraMenuItems.push({
    //   name: "Edit Column Type",
    //   action: (v) => {
    //     setEditColumnType(column);
    //   },
    // });
    // extraMenuItems.push({
    //   name: "Delete Column",
    //   action: (v) => {
    //     setDeleteColumn(column);
    //   },
    // });

    return [...extraMenuItems, ...menuItems];
  };

  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        const { request } = params;

        // Calculate the page number based on the row start
        const pageNumber = Math.floor(request.startRow / 10);

        try {
          // Make the POST request
          const { data } = await axios.get(
            endpoints.develop.individualExperimentDataset(
              individualExperimentId,
            ),
            { params: { current_page_index: pageNumber } },
          );

          const columns =
            data?.result?.column_config || data?.result?.columnConfig;

          const grouping = {};

          for (const eachCol of columns) {
            const colSourceId = eachCol?.source_id || eachCol?.sourceId;
            const colOriginType = eachCol?.origin_type || eachCol?.originType;
            if (
              colSourceId &&
              (colOriginType === "evaluation" ||
                colOriginType === "evaluation_reason")
            ) {
              if (!grouping[colSourceId]) {
                grouping[colSourceId] = [eachCol];
              } else {
                grouping[colSourceId].push(eachCol);
              }
            } else {
              grouping[eachCol?.id] = [eachCol];
            }
          }

          const columnMap = [];
          const bottomRow = {};

          const refresh = [];

          for (const [_, cols] of Object.entries(grouping)) {
            if (cols.length == 1) {
              const eachCol = cols[0];
              columnMap.push(
                getIndividualExperimentColumnConfig({
                  eachCol,
                  getMainMenuItems,
                  isHoverButtonVisible,
                }),
              );
              if (RefreshStatus.includes(eachCol?.status)) {
                refresh.push(eachCol.id);
              }
              bottomRow[eachCol.id] =
                eachCol?.averageScore !== null &&
                eachCol?.averageScore !== undefined
                  ? `Average : ${eachCol?.averageScore.toFixed(2)}%`
                  : "";
            } else {
              const eachCol = cols.find((v) => v?.originType === "evaluation");
              const children = cols.map((v) =>
                getIndividualExperimentColumnConfig({
                  eachCol: v,
                  getMainMenuItems,
                  isHoverButtonVisible,
                }),
              );
              columnMap.push(
                getIndividualExperimentColumnConfig({
                  eachCol,
                  getMainMenuItems,
                  children,
                  isHoverButtonVisible,
                }),
              );
              if (RefreshStatus.includes(eachCol?.status)) {
                refresh.push(eachCol.id);
              }
              bottomRow[eachCol.id] = eachCol?.averageScore
                ? `Average : ${eachCol?.averageScore}%`
                : "";
            }
          }

          if (refresh.length > 0) {
            if (!isRefreshing) setIsRefreshing(refresh);
          } else {
            setIsRefreshing(null);
          }

          setColumnDefs([...columnMap]);
          setPinnedBottomRowData([
            {
              checkbox: "",
              ...bottomRow,
            },
          ]);
          const rows = data?.result?.table;
          params.success({
            rowData: rows,
            rowCount: data?.result?.metadata?.total_rows,
          });
          storeAllDisplayedRows();
        } catch (error) {
          setIsRefreshing(null);
          params.fail();
        }
      },

      getRowId: (data) => data.row_id,
    }),

    // eslint-disable-next-line react-hooks/exhaustive-deps
    [individualExperimentId], // Dependencies array - rerun when `experimentId` changes
  );

  const onColumnChanged = useCallback(
    (params) => {
      // Only process when column move is finished
      if (!params.finished && params.type === "columnMoved") {
        return;
      }

      // Get new column order
      const newColumnOrder = params.api
        .getColumnState()
        .filter(
          ({ colId }) => colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN,
        );

      setColumnDefs((existingColumnOrder) => {
        const colDefMap = existingColumnOrder.reduce((acc, col) => {
          acc[col.field] = col;
          return acc;
        }, {});

        const newState = newColumnOrder.map((state) => {
          const pinned =
            params.type === "columnPinned" &&
            params.column.colId === state.colId
              ? params.pinned
              : colDefMap[state.colId].pinned;

          const visible =
            params.type === "columnVisible" &&
            params.column.colId === state.colId
              ? params.visible
              : !colDefMap[state.colId].hide;

          return { ...colDefMap[state.colId], pinned, hide: !visible };
        });

        return newState;
      });

      const filteredColumnOrder = [];
      const columnConfig = {};

      for (const column of newColumnOrder) {
        if (column.colId !== "checkbox") {
          filteredColumnOrder.push(column.colId);
          columnConfig[column.colId] = {
            is_visible: !column.hide,
            is_frozen: column.pinned,
          };
        }
      }

      // @ts-ignore
      updateDataset({
        dataset_name: currentDataset?.name,
        column_order: filteredColumnOrder,
        column_config: columnConfig,
      });
    },
    [currentDataset?.name, updateDataset],
  );

  const refreshGrid = (options) => {
    gridApiRef.current?.api?.refreshServerSide(options);
  };

  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        flex: 1,
        padding: "12px",
        height: "100%",
        overflowY: "hidden",
      }}
    >
      <ExperimentDetailDrawer
        open={Boolean(expandRow)}
        onClose={() => setExpandRow(null)}
        row={expandRow}
        columnConfig={columnConfig}
        allRows={allRows}
        setExpandRow={setExpandRow}
        totalCount={totalRows}
        setAllRows={setAllRows}
        refreshGrid={refreshGrid}
      />
      {experimentMeta ? (
        <EvaluationDrawer
          open={evaluateOpen}
          onClose={() => setEvaluateOpen(false)}
          allColumns={allColumnOptions}
          refreshGrid={refreshGrid}
          id={experimentMeta?.dataset}
        />
      ) : (
        // <RunEvaluation
        //   open={evaluateOpen}
        //   onClose={() => setEvaluateOpen(false)}
        //   allColumns={allColumnOptions}
        //   experimentEval={{
        //     individualExperimentId,
        //     baseColumnId: experimentMeta?.column,
        //   }}
        // />
        <></>
      )}
      <AgGridReact
        ref={gridApiRef}
        columnDefs={columnDefs}
        defaultColDef={ExperimentDataDefaultColDef}
        paginationAutoPageSize={true}
        suppressRowClickSelection={true}
        paginationPageSizeSelector={true}
        rowModelType="serverSide"
        serverSideDatasource={dataSource}
        pagination={true}
        cacheBlockSize={10}
        // rowSelection={{ mode: "multiRow" }}
        theme={agTheme}
        onColumnMoved={onColumnChanged}
        onColumnPinned={onColumnChanged}
        onColumnVisible={onColumnChanged}
        getRowHeight={(params) => {
          if (params.node.rowPinned === "bottom") {
            return 40;
          }
          return 120;
        }}
        suppressServerSideFullWidthLoadingRow={true}
        serverSideInitialRowCount={10}
        pinnedBottomRowData={pinnedBottomRowData}
        // onRowClicked={(params) => {
        //   setExpandRow({
        //     ...params.data,
        //     index: params.rowIndex,
        //   });
        // }}
      />
    </Box>
  );
};

export default IndividualExperimentDataView;
