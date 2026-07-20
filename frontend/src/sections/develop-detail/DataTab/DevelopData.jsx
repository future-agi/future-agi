import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, {
  useCallback,
  useMemo,
  useState,
  useRef,
  useEffect,
} from "react";
import { useParams } from "react-router-dom";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import axios, { endpoints } from "src/utils/axios";
import { DevelopDataBlockedChangeDataType } from "src/utils/constant";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import PropTypes from "prop-types";
import DevelopFilterBox from "./DevelopFilters/DevelopFilterBox";
import { getRandomId, preventHeaderSelection } from "src/utils/utils";
import {
  compareFilterChange,
  DefaultFilter,
  transformFilter,
  validateFilter,
} from "./DevelopFilters/common";
import EditColumnName from "./EditColumnName";
import EditColumnType from "./EditColumnType";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { getColumnConfig, getTypeDefinitions } from "./common";
import "./developDataGrid.css";
import DatapointDrawer from "./DatapointDrawer/DatapointDrawer";
import ConfirmDeleteColumn from "./DeleteColumn";
import RerunDependentColumns from "./RerunDependentColumns";
import SingleImageViewerProvider from "../Common/SingleImageViewer/SingleImageViewerProvider";
import AddRowData from "./AddRowData";
import AddEvaluationFeeback from "./AddEvaluationFeeback/AddEvaluationFeeback";
import ImprovePrompt from "./ImprovePrompt/ImprovePrompt";
import {
  reorderMenuList,
  setMenuIcons,
} from "src/utils/MenuIconSet/setMeniIcons";
import { menuIcons } from "src/utils/MenuIconSet/svgIcons";
import DoubleClickEditCell from "./DoubleClickEditCell/DoubleClickEditCell";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import useWavesurferCache from "src/hooks/use-wavesurfer-cache";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import TopBanner from "./TopBanner";
import { defaultRowHeightMapping } from "src/utils/constants";
import DataTabStatusBar from "./DataTabStatusBar";
import JsonCellEditor from "./DoubleClickEditCell/JsonCellEditor";
import { format } from "date-fns";
import logger from "src/utils/logger";
import { useRunPromptStoreShallow } from "../states";
import { APP_CONSTANTS } from "src/utils/constants";
import { useAuthContext } from "src/auth/hooks";
import { ROLES } from "src/utils/rolePermissionMapping";

const menuOrder = [
  "Show Reasoning",
  "Hide Reasoning",
  "Configure Eval",
  "Configure Run",
  "Edit Column Name",
  "Edit Column Type",
  "Delete Column",
  "separator",
  "Pin Column",
  "Sort Ascending",
  "Sort Descending",
  "separator",
  // "Choose Columns",
  "Autosize This Column",
  "Autosize All Columns",
  "Reset Columns",
];

const RefreshStatus = [
  "Running",
  "NotStarted",
  "Editing",
  "ExperimentEvaluation",
  "PartialRun",
];

const DevelopData = React.forwardRef(
  ({
    columnDefs,
    setColumnDefs,
    setColumns,
    setDevelopFilterOpen,
    developFilterOpen,
    setConfigureEval,
    onSelectionChanged,
    setSelectedAll,
    searchQuery,
    cellHeight,
    setIsFilterApplied,
    setIsRows,
    gridApiRef,
  }) => {
    const { role } = useAuthContext();
    const isViewerRole =
      role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
    const queryClient = useQueryClient();
    const editCellRef = useRef(null);
    const { dataset } = useParams();
    const datasetRef = useRef(dataset);
    const [editCell, setEditCell] = useState(null);
    const [editColumn, setEditColumn] = useState(null);
    const [editColumnType, setEditColumnType] = useState(null);
    const [deleteColumn, setDeleteColumn] = useState(null);
    const [feedBack, setFeedBack] = useState(null);
    const [improvement, setImprovement] = useState(null);
    const [activeRow, setActiveRow] = useState(null);
    const [allRows, setAllRows] = useState([]);
    const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.dataGrid);
    // const [pageState, setPageState] = useSearchParams({
    //   datasets: [dataset],
    // });
    const [showSummary, setShowSummary] = useState(
      JSON.parse(localStorage.getItem("showSummary")) || [],
    );

    const [datapointDrawerData, setDatapointDrawerData] = useState(null);
    const [pinnedBottomRowData] = useState([]);
    const [isData, setIsData] = useState(true);
    const performedClicks = useRef(0);
    const clickTimeout = useRef(null);
    const shouldRefreshRef = useRef(false);
    const [evalDrawer, setEvalDrawer] = useState(false);
    const isRefreshingColumns = useRef(null);

    const [rowNewData, setRowNewData] = useState();
    const [currentColumn, setCurrentColumn] = useState(null);
    const [totalRows, setTotalRows] = useState(0);
    const setOpenRunPrompt = useRunPromptStoreShallow(
      (s) => s.setOpenRunPrompt,
    );

    const {
      clearWaveSurferCache,
      getWaveSurferInstance,
      removeWaveSurferInstance,
      storeWaveSurferInstance,
      updateWaveSurferInstance,
    } = useWavesurferCache();

    useEffect(() => {
      // Return a cleanup function to clear cache on component unmount
      return () => {
        clearWaveSurferCache();
      };
    }, [clearWaveSurferCache]); // Dependency array includes the stable callback

    const [filters, setFilters] = useState([
      { ...DefaultFilter, id: getRandomId() },
    ]);

    const refreshGrid = () => {
      // refreshRowsManual();
      gridApiRef?.current?.api?.refreshServerSide({});
    };

    useEffect(() => {
      const hasActiveFilter = filters?.some((f) =>
        f.filterConfig?.filterValue && Array.isArray(f.filterConfig.filterValue)
          ? f.filterConfig.filterValue.length > 0
          : f.filterConfig.filterValue !== "",
      );
      setIsFilterApplied(hasActiveFilter);
    }, [filters, setIsFilterApplied]);

    const allRef = useRef(false);

    const prevFiltersRef = useRef(filters);
    preventHeaderSelection();

    useEffect(() => {
      if (editColumn) {
        trackEvent(Events.editColumnNameClicked);
      }
      if (editColumnType) {
        trackEvent(Events.editColumnTypeClicked);
      }
      if (deleteColumn) {
        trackEvent(Events.deleteColumnClicked);
      }
    }, [editColumn, editColumnType, deleteColumn]);

    useEffect(() => {
      const compare = compareFilterChange(prevFiltersRef.current, filters);
      if (!compare) {
        gridApiRef?.current?.api?.refreshServerSide();
        // refreshRowsManual();
      }
      prevFiltersRef.current = filters;
    }, [filters]);

    useEffect(() => {
      const interval = setInterval(() => {
        if (isRefreshingColumns.current) {
          refreshRowsManual();
        }
      }, 10000);
      return () => clearInterval(interval);
    }, []);

    const { data: datasetList } = useDevelopDatasetList();

    const currentDataset = datasetList?.find((v) => v.datasetId === dataset);

    const postProcessPopup = useCallback((params) => {
      if (params.type !== "columnMenu") {
        return;
      }

      const ePopup = params.ePopup;
      ePopup.style.backgroundColor = "var(--bg-paper, #fff)";
      ePopup.style.borderRadius = "12px";
      ePopup.style.border = "1px solid var(--border-default, #e5e7eb)";
      ePopup.style.padding = "16px";
      ePopup.style.margin = "0px";
      ePopup.style.boxShadow = "0px 4px 10px rgba(0, 0, 0, 0.1)";
      ePopup.style.fontFamily = "Inter, sans-serif";
      ePopup.style.fontWeight = 400;
      ePopup.style.color = "var(--text-primary)";

      const menuItemsList = ePopup.querySelectorAll(".ag-menu-list");
      menuItemsList.forEach((item) => {
        item.style.padding = "0px";
      });

      const menuItems = ePopup.querySelectorAll(".ag-menu-option");
      menuItems.forEach((item) => {
        item.style.height = "30px";
        item.style.minHeight = "30px";
        item.style.padding = "0px";
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

      const menuParts = ePopup.querySelectorAll(".ag-menu-option-part");
      menuParts.forEach((part) => {
        part.style.padding = "0px 12px";
        part.style.margin = "0";
        part.style.lineHeight = "normal";
      });

      const elements = ePopup.querySelectorAll('span[data-ref="eName"]');
      elements.forEach((element) => {
        if (
          element.textContent.trim() === "Show Reasoning" ||
          element.textContent.trim() === "Hide Reasoning"
        ) {
          element.style.color = "var(--primary-main)";
        }
        if (element.textContent.trim() === "Delete Column") {
          element.style.color = "#fa0c0c";
        }
        element.style.fontWeight = 400;
        element.style.fontSize = "12px";
      });

      const separatorLines = ePopup.querySelectorAll(".ag-menu-separator-part");
      separatorLines.forEach((line) => {
        line.style.height = "1px";
      });

      const icons = ePopup.querySelectorAll(".ag-menu-option-icon");
      icons.forEach((icon) => {
        icon.style.padding = "0";
      });
    }, []);

    // Grid Options
    const defaultColDef = useMemo(
      () => ({
        lockVisible: true,
        filter: false,
        resizable: true,
        cellStyle: {
          padding: 0,
          height: "100%",
          display: "flex",
          flex: 1,
          flexDirection: "column",
        },
      }),
      [],
    );

    const allColumns = useMemo(() => {
      return columnDefs.filter((col) => col.field !== "checkbox");
    }, [columnDefs]);

    const statusBar = useMemo(() => ({
      statusPanels: [
        {
          statusPanel: DataTabStatusBar,
          align: "left",
          key: "rowCounter",
        },
      ],
    }));

    const { mutate: updateCellValue } = useMutation({
      mutationFn: (d) =>
        axios.post(endpoints.develop.updateCellValue(dataset), d),
      onSuccess: () => {
        if (shouldRefreshRef.current) {
          refreshGrid();
          shouldRefreshRef.current = false; // reset after refresh
        }
      },
    });

    const { mutate: updateDataset } = useMutation({
      mutationFn: (d) => axios.put(endpoints.develop.updateDataset(dataset), d),
    });

    const { mutate: deleteColumnMutate, isPending: isDeletingColumn } =
      useMutation({
        mutationFn: (d) =>
          axios.delete(endpoints.develop.deleteColumn(dataset, d)),
        onSuccess: () => {
          trackEvent(Events.deleteColumnSuccessful, {
            [PropertyName.columnType]: {
              dataset: currentDataset?.name,
              columnId: deleteColumn.id,
              deleted_column_name: deleteColumn.name,
            },
          });
          setDeleteColumn(null);
          // refreshRowsManual();
          gridApiRef?.current?.api?.refreshServerSide({ purge: true });
          refreshColumns();
          refreshEvalsList();
        },
      });

    const validatedFilters = useMemo(() => {
      return filters.filter(validateFilter).map(transformFilter);
    }, [filters]);

    // Polling to check if data is added every 5 seconds when no data exists
    useEffect(() => {
      let pollingInterval;

      if (!isData) {
        pollingInterval = setInterval(async () => {
          try {
            const { data } = await axios.get(
              endpoints.develop.getDatasetDetail(dataset),
              {
                params: {
                  current_page_index: 0,
                  filters: validatedFilters,
                },
              },
            );

            const hasData = data?.result?.columnConfig?.length > 0;
            if (hasData) {
              setIsData(true);
              setIsRows(data?.result?.metadata?.total_rows > 0);
              gridApiRef?.current?.api?.refreshServerSide({ purge: true });
            }
          } catch (error) {
            logger.warn("Error:", error);
          }
        }, 5000); // Poll every 5 seconds
      }

      return () => {
        if (pollingInterval) {
          clearInterval(pollingInterval);
        }
      };
    }, [isData, dataset, validatedFilters]);

    const getMainMenuItems = (params) => {
      const allMenuItems = setMenuIcons(params, currentDataset?.name); // Pass dataset name
      const menuItems = allMenuItems.slice(0);
      // const menuItems = params.defaultItems.slice(0);
      const column = params.column.colDef.col;
      const extraMenuItems = [];
      if (column.originType === "evaluation") {
        extraMenuItems.push({
          name: "Configure Eval",
          action: () => {
            setConfigureEval({ id: column?.sourceId, evalType: "user" });
          },
          icon: menuIcons["Configure Eval"],
        });
      }
      if (column.originType === "run_prompt") {
        extraMenuItems.push({
          name: "Configure Run",
          action: () => {
            setOpenRunPrompt(column);
          },
          icon: menuIcons["Configure Run"],
        });
      }
      if (column.originType === "evaluation") {
        extraMenuItems.push({
          name: showSummary.includes(column.id)
            ? "Hide Reasoning"
            : "Show Reasoning",
          action: () => {
            toggleSummary(column);
          },
          icon: menuIcons["Show Reasoning"],
        });
      }
      if (!isViewerRole) {
        extraMenuItems.push({
          name: "Edit Column Name",
          action: () => {
            setEditColumn(column);
          },
          icon: menuIcons["Edit Column Name"],
        });
        if (!DevelopDataBlockedChangeDataType.includes(column.originType)) {
          extraMenuItems.push({
            name: "Edit Column Type",
            action: () => {
              setEditColumnType(column);
            },
            icon: menuIcons["Edit Column Type"],
          });
        }
        extraMenuItems.push({
          name: "Delete Column",
          action: () => {
            setDeleteColumn(column);
          },
          icon: menuIcons["Delete Column"],
        });
      }
      const mainMenuItems = [...extraMenuItems, ...menuItems];
      const separatorAfter = [
        "Show Reasoning",
        "Delete Column",
        "Sort Descending",
      ];
      return reorderMenuList(mainMenuItems, menuOrder, separatorAfter);
    };

    const toggleSummary = (column) => {
      setShowSummary((prevShowSummary) => {
        const newShowSummary = prevShowSummary.includes(column.id)
          ? prevShowSummary.filter((id) => id !== column.id)
          : [...prevShowSummary, column.id];
        localStorage.setItem("showSummary", JSON.stringify(newShowSummary));
        return newShowSummary;
      });
    };

    const setColumnData = (
      data,
      setCols = true,
      setRefresh = true,
      setBottomRow = true,
    ) => {
      const columns = data?.result?.columnConfig;
      if (columns.length == 0) {
        setIsData(false);
        setIsRows(false);
      }

      const grouping = {};

      for (const eachCol of columns) {
        if (
          eachCol?.sourceId &&
          (eachCol?.originType === "evaluation" ||
            eachCol?.originType === "evaluation_reason")
        ) {
          if (!grouping[eachCol?.sourceId]) {
            grouping[eachCol?.sourceId] = [eachCol];
          } else {
            grouping[eachCol?.sourceId].push(eachCol);
          }
        } else {
          grouping[eachCol?.id] = [eachCol];
        }
      }

      const columnMap = [];
      const bottomRow = {};

      const refresh = [];

      for (const [_, cols] of Object.entries(grouping)) {
        if (cols.length === 1) {
          const eachCol = cols[0];
          columnMap.push(
            getColumnConfig({
              eachCol,
              getMainMenuItems,
              setFeedBack,
              setImprovement,
              setDatapointDrawerData,
              getWaveSurferInstance,
              storeWaveSurferInstance,
              removeWaveSurferInstance,
              updateWaveSurferInstance,
              setRowNewData,
              editCellRef,
              setEditCell,
              editCell,
              onCellValueChanged,
              isViewerRole,
            }),
          );
          if (RefreshStatus.includes(eachCol?.status)) {
            refresh.push(eachCol.id);
          }
          bottomRow[eachCol.id] =
            eachCol?.averageScore === 0 || eachCol?.averageScore ? (
              `Average : ${eachCol?.averageScore}%`
            ) : eachCol?.metadata?.averageCost ||
              eachCol?.metadata?.averageLatency ||
              eachCol?.metadata?.averageTokens ? (
              <div
                style={{
                  display: "flex",
                  gap: 2,
                  lineHeight: "1.5",
                  alignItems: "center",
                }}
              >
                <Iconify
                  icon="material-symbols:schedule-outline"
                  sx={{
                    display: "flex",
                    gap: 0.5,
                    alignItems: "center",
                    width: 12,
                    height: 12,
                    marginRight: "2px",
                  }}
                />
                {Math.round(eachCol?.metadata?.averageLatency)}ms
                <SvgColor
                  src="/assets/icons/components/ic_coin.svg"
                  sx={{
                    width: 15,
                    height: 15,
                    marginLeft: "16px",
                    marginRight: "2px",
                    color: "text.primary",
                  }}
                />
                {Math.round(eachCol?.metadata?.averageTokens)}
                <Iconify
                  icon="material-symbols:attach-money"
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    width: 12,
                    height: 12,
                    marginLeft: "16px",
                    marginRight: "-2px",
                  }}
                />
                {Number(eachCol?.metadata?.averageCost).toFixed(6)}
              </div>
            ) : (
              ""
            );
        } else {
          let eachCol = cols[0];
          let children = null;
          if (showSummary.includes(eachCol.id)) {
            eachCol = cols.find((v) => v?.originType === "evaluation");
            children = cols.map((v) =>
              getColumnConfig({
                eachCol: v,
                getMainMenuItems,
                setFeedBack,
                setImprovement,
                setDatapointDrawerData,
                getWaveSurferInstance,
                storeWaveSurferInstance,
                removeWaveSurferInstance,
                updateWaveSurferInstance,
                setRowNewData,
                editCellRef,
                setEditCell,
                editCell,
                onCellValueChanged,
                isViewerRole,
              }),
            );
          }
          columnMap.push(
            getColumnConfig({
              eachCol,
              getMainMenuItems,
              ...(children && { children }),
              setFeedBack,
              setImprovement,
              setDatapointDrawerData,
              getWaveSurferInstance,
              storeWaveSurferInstance,
              removeWaveSurferInstance,
              updateWaveSurferInstance,
              setRowNewData,
              editCellRef,
              setEditCell,
              editCell,
              onCellValueChanged,
              isViewerRole,
            }),
          );
          if (RefreshStatus.includes(eachCol?.status)) {
            refresh.push(eachCol.id);
          }
          bottomRow[eachCol.id] =
            eachCol?.averageScore === 0 || eachCol?.averageScore
              ? `Average : ${eachCol?.averageScore}%`
              : "";
        }
      }

      if (setRefresh) {
        if (refresh.length > 0) {
          if (!isRefreshingColumns.current) {
            isRefreshingColumns.current = refresh;
          }
        } else {
          isRefreshingColumns.current = null;
        }
      }

      if (setCols) {
        setColumnDefs([...columnMap]);
      }

      if (setBottomRow) {
        gridApiRef?.current?.api?.setGridOption("pinnedBottomRowData", [
          {
            checkbox: "",
            ...bottomRow,
          },
        ]);
      }

      return refresh;
    };

    const refreshRowsManual = async () => {
      const totalPages = Object.keys(
        gridApiRef?.current?.api?.getCacheBlockState(),
      ).length;

      for (let p = 0; p < totalPages; p++) {
        try {
          // Fetch updated column data from your API
          const { data } = await axios.get(
            endpoints.develop.getDatasetDetail(dataset),
            {
              params: {
                current_page_index: p,
                filters: validatedFilters,
              },
            },
          );
          const { data: columnData } = await axios.get(
            endpoints.develop.getDatasetDetail(dataset),
            {
              params: {
                column_config_only: true,
              },
            },
          );
          queryClient.setQueryData(["develop-data"], data);

          const rows = data?.result?.table;

          const transaction = {
            update: rows,
          };

          if (gridApiRef.current?.api) {
            gridApiRef.current.api.applyServerSideTransaction(transaction);
          }

          setColumnData(columnData, false, true, true);
        } catch (e) {
          logger.error("Error:", e);
        }
      }
    };

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

    const dataSource = useMemo(
      () => ({
        getRows: async (params) => {
          const { request } = params;
          onSelectionChanged(null);
          setSelectedAll(false);

          const pageNumber = Math.floor(request.startRow / 10);

          try {
            const { data } = await axios.get(
              endpoints.develop.getDatasetDetail(dataset),
              {
                params: {
                  current_page_index: pageNumber,
                  filters: validatedFilters,
                  sort: request?.sortModel?.map(({ colId, sort }) => ({
                    columnId: colId,
                    type: sort === "asc" ? "ascending" : "descending",
                  })),
                  ...(searchQuery && {
                    search: {
                      key: searchQuery,
                      type: ["text", "image", "audio"],
                    },
                  }),
                },
              },
            );

            queryClient.setQueryData(["develop-data"], data);

            const rows = data?.result?.table;
            const totalRows = data?.result?.metadata.total_rows;
            setTotalRows(totalRows);
            setIsRows(Boolean(totalRows));
            const hasMoreData = request.startRow + rows.length < totalRows;
            const rowCountToReturn = hasMoreData ? undefined : totalRows;

            gridApiRef.current.api.setGridOption("context", {
              totalRowCount: totalRows,
            });
            params.success({
              rowData: rows,
              rowCount: rowCountToReturn,
            });
            storeAllDisplayedRows();

            if (allRef.current) {
              rows?.map((item) => {
                onSelectionChanged({ data: item });
              });
            }
          } catch (error) {
            isRefreshingColumns.current = null;
            params.fail();
          }
        },
      }),
      [dataset, validatedFilters, searchQuery],
    );

    const { data: columnConfigData } = useQuery({
      queryFn: () =>
        axios.get(endpoints.develop.getDatasetDetail(dataset), {
          params: {
            column_config_only: true,
          },
        }),
      queryKey: ["dataset-column-config", dataset, showSummary],
      refetchOnWindowFocus: false,
      refetchOnMount: false,
      refetchOnReconnect: false,
      select: (data) => data.data,
      enabled: !!dataset,
    });

    useEffect(() => {
      if (!columnConfigData) return;
      setColumnData(columnConfigData);
      setColumns(columnConfigData?.result?.columnConfig);
    }, [columnConfigData, setColumns]);

    const refreshColumns = () => {
      queryClient.invalidateQueries({
        queryKey: ["dataset-column-config", dataset],
        type: "all",
      });
    };

    const refreshEvalsList = () => {
      queryClient.invalidateQueries({
        queryKey: ["develop", "user-eval-list", dataset],
      });
    };

    const onCellValueChanged = (params) => {
      if (params?.type === "cellValueChanged" && params?.source === undefined) {
        return;
      }
      const columnId = params?.column?.colId;
      const rowId = params?.data?.rowId;
      const newValue = params?.newValue;
      const dataType = params?.column?.colDef?.dataType;

      const gridApi = params.api;
      const rowNode = gridApi.getRowNode(rowId);

      shouldRefreshRef.current =
        searchQuery && ["text", "array"].includes(dataType);

      if (rowNode) {
        try {
          if (newValue instanceof File) {
            const tempUrl = URL.createObjectURL(newValue);
            rowNode.setDataValue(columnId, tempUrl);

            const formData = new FormData();
            formData.append("columnId", columnId);
            formData.append("rowId", rowId);
            formData.append("newValue", newValue);

            updateCellValue(formData);
          } else if (
            typeof newValue === "string" &&
            newValue.startsWith("data:image/")
          ) {
            rowNode.setDataValue(columnId, newValue);

            updateCellValue({
              columnId,
              rowId,
              newValue,
            });
          } else if (dataType == "datetime") {
            const date = new Date(newValue);
            rowNode.setDataValue(columnId, date);
            const formattedDate = format(date, "yyyy-MM-dd HH:mm:ss");
            updateCellValue({
              columnId,
              rowId,
              newValue: formattedDate,
            });
          } else {
            const formattedValue =
              typeof newValue === "object" && newValue !== null
                ? JSON.stringify(newValue)
                : newValue?.toString() ?? "";
            rowNode.setDataValue(columnId, formattedValue);
            updateCellValue({
              columnId,
              rowId,
              newValue: formattedValue,
            });
          }
        } catch (e) {
          logger.warn("Warning:", e);
        }
      }
    };

    const onColumnChanged = useCallback(
      (params) => {
        if (!params.finished && params.type === "columnMoved") {
          return;
        }

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
              params.column?.colId === state.colId
                ? params?.pinned
                : colDefMap[state.colId]?.pinned;

            const visible =
              params.type === "columnVisible" &&
              params.column?.colId === state.colId
                ? params.visible
                : !colDefMap[state.colId]?.hide;

            return { ...colDefMap[state.colId], pinned, hide: !visible };
          });

          return newState;
        });
        if (params?.type === "columnPinned" && params?.pinned) {
          if (params?.pinned === "left") {
            trackEvent(Events.columnPinnedClicked, {
              [PropertyName.status]: "pin left",
            });
          } else if (params?.pinned === "right") {
            trackEvent(Events.columnPinnedClicked, {
              [PropertyName.status]: "pin right",
            });
          }
        }

        const filteredColumnOrder = [];
        const columnConfig = {};
        const hiddenColumns = [];

        // for (let column of newColumnOrder) {
        //   if (column.colId !== "checkbox") {
        //     filteredColumnOrder.push(column.colId);
        //     columnConfig[column.colId] = {
        //       is_visible: !column.hide,
        //       is_frozen: Boolean(column.pinned),
        //     };
        //   }
        // }
        for (const column of newColumnOrder) {
          if (column.colId !== "checkbox") {
            filteredColumnOrder.push(column.colId);
            columnConfig[column.colId] = {
              is_visible: !column.hide,
              is_frozen: column?.pinned,
            };
            if (column.hide) {
              const columnDef = params.api.getColumnDef(column.colId); // Get column definition
              const columnName =
                columnDef?.headerName || columnDef?.field || column.colId; // Get column name
              hiddenColumns.push(columnName);
            }
          }
        }
        trackEvent(Events.columnDeselectionSuccessful, {
          dataset_name: currentDataset?.name,
          deselected_columms_name: hiddenColumns,
        });

        // @ts-ignore
        updateDataset({
          dataset_name: currentDataset?.name,
          column_order: filteredColumnOrder,
          column_config: columnConfig,
        });
      },
      [currentDataset],
    );

    const dataTypeDefinitions = useMemo(getTypeDefinitions, []);

    const reset = (options) => {
      // refreshRowsManual();
      gridApiRef?.current?.api?.refreshServerSide({});

      if (options?.resetFilters) {
        setFilters([{ ...DefaultFilter, id: getRandomId() }]);
      }
    };

    const debounceCellClick = (handler, event, delay = 250) => {
      performedClicks.current++;
      clickTimeout.current = setTimeout(() => {
        if (performedClicks.current === 1) {
          performedClicks.current = 0;
          handler(event);
        } else {
          performedClicks.current = 0;
        }
      }, delay);
      if (performedClicks.current > 1 && clickTimeout.current) {
        clearTimeout(clickTimeout.current);
      }
    };

    const handleDrawerClose = () => {
      setDatapointDrawerData(false);
      setEvalDrawer(false);
      setActiveRow(null);
      if (datasetRef.current !== dataset) {
        setAllRows([]);
        datasetRef.current = dataset;
      }
    };

    useEffect(() => {
      handleDrawerClose();
    }, [dataset]);

    const doubleClickCellEdit = (event) => {
      if (isViewerRole) return;
      const dataType = event?.colDef?.dataType;
      const originType = event?.colDef?.originType;
      const colId = event?.column?.colId;

      if (
        (dataType === "audio" || dataType === "image") &&
        colId !== APP_CONSTANTS.AG_GRID_SELECTION_COLUMN &&
        ![
          "run_prompt",
          "evaluation",
          "optimization",
          "annotation_label",
          "evaluation_reason",
        ].includes(originType)
      ) {
        editCellRef.current = event.event;
        setEditCell({ ...event });
      }
    };

    return (
      <AudioPlaybackProvider>
        <DoubleClickEditCell
          open={Boolean(editCell)}
          onClose={() => setEditCell(null)}
          params={editCell}
          onCellValueChanged={onCellValueChanged}
        />
        <AddEvaluationFeeback
          open={Boolean(feedBack)}
          onClose={() => setFeedBack(null)}
          data={feedBack}
          refreshGrid={() => {
            refreshGrid();
            refreshColumns();
          }}
        />
        <ImprovePrompt
          open={Boolean(improvement)}
          onClose={() => setImprovement(null)}
          data={improvement}
          allColumns={allColumns}
        />
        <EditColumnName
          open={Boolean(editColumn)}
          onClose={() => setEditColumn(null)}
          column={editColumn}
          reset={reset}
          refreshColumns={refreshColumns}
        />
        <EditColumnType
          open={Boolean(editColumnType)}
          onClose={() => setEditColumnType(null)}
          column={editColumnType}
          reset={reset}
        />
        <RerunDependentColumns />
        <ConfirmDeleteColumn
          open={Boolean(deleteColumn)}
          onClose={() => setDeleteColumn(null)}
          onConfirm={() => {
            deleteColumnMutate(deleteColumn?.id);
          }}
          isLoading={isDeletingColumn}
        />
        <DatapointDrawer
          open={Boolean(datapointDrawerData)}
          onClose={handleDrawerClose}
          setDataPointDrawerData={setDatapointDrawerData}
          datapoint={datapointDrawerData ?? {}}
          allColumns={allColumns}
          rowIndex={datapointDrawerData?.rowIndexData}
          setEvalDrawer={setEvalDrawer}
          evalDrawer={evalDrawer}
          validatedFilters={validatedFilters}
          setActiveRow={setActiveRow}
          totalCount={totalRows}
          rowNewData={rowNewData}
          currentColumn={currentColumn}
          setRowNewData={setRowNewData}
          allRows={allRows}
          setAllRows={setAllRows}
        />
        <Box
          className="ag-theme-quartz"
          sx={{
            flex: 1,
            padding: "12px",
            paddingTop: "8px",
            backgroundColor: "background.paper",
          }}
        >
          <DevelopFilterBox
            setDevelopFilterOpen={setDevelopFilterOpen}
            developFilterOpen={developFilterOpen}
            filters={filters}
            setFilters={setFilters}
            allColumns={allColumns}
          />

          <SingleImageViewerProvider>
            {isData === true ? (
              <>
                <TopBanner />
                <AgGridReact
                  rowHeight={defaultRowHeightMapping[cellHeight]?.height}
                  getRowHeight={(params) => {
                    return params.node.rowPinned === "bottom"
                      ? 40
                      : defaultRowHeightMapping[cellHeight]?.height;
                  }}
                  onHeaderCellClicked={(event) => {
                    if (
                      event.column.colId ===
                      APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                    ) {
                      const displayedNodes = [];
                      event.api.forEachNode((node) => {
                        if (node.displayed) {
                          displayedNodes.push(node);
                        }
                      });
                      const allSelected = displayedNodes.every((node) =>
                        node.isSelected(),
                      );

                      if (allSelected) {
                        event.api.deselectAll();
                      } else {
                        event?.api.selectAll();
                      }
                    }
                  }}
                  onColumnHeaderClicked={(event) => {
                    if (
                      event?.column?.colId ===
                      APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                    ) {
                      const displayedNodes = [];
                      event?.api.forEachNode((node) => {
                        if (node.displayed) {
                          displayedNodes.push(node);
                        }
                      });
                      const allSelected = displayedNodes.every((node) =>
                        node.isSelected(),
                      );

                      if (allSelected) {
                        allRef.current = true;
                      } else {
                        allRef.current = false;
                      }
                    }
                  }}
                  rowSelection={{ mode: "multiRow" }}
                  selectionColumnDef={{ pinned: "left" }}
                  ref={gridApiRef}
                  components={{
                    JsonCellEditor: JsonCellEditor,
                  }}
                  theme={agTheme}
                  columnDefs={columnDefs}
                  defaultColDef={defaultColDef}
                  pagination={false}
                  cacheBlockSize={30}
                  maxBlocksInCache={5}
                  rowBuffer={10}
                  suppressServerSideFullWidthLoadingRow={true}
                  serverSideInitialRowCount={30}
                  // suppressRowClickSelection={true}
                  statusBar={statusBar}
                  rowModelType="serverSide"
                  serverSideDatasource={dataSource}
                  onCellValueChanged={onCellValueChanged}
                  onColumnMoved={onColumnChanged}
                  onColumnPinned={onColumnChanged}
                  onColumnVisible={onColumnChanged}
                  dataTypeDefinitions={dataTypeDefinitions}
                  pinnedBottomRowData={pinnedBottomRowData}
                  postProcessPopup={postProcessPopup}
                  isApplyServerSideTransaction={() => true}
                  stopEditingWhenCellsLoseFocus
                  onCellDoubleClicked={doubleClickCellEdit}
                  suppressRowTransform={true}
                  suppressAnimationFrame={true}
                  getRowId={({ data }) => {
                    return data.rowId;
                  }}
                  onCellClicked={(params) => {
                    setActiveRow(null);
                    if (window.__audioClick) {
                      window.__audioClick = false;
                      return;
                    }
                    if (window.__jsonViewerClick) {
                      window.__jsonViewerClick = false;
                      return;
                    }
                    if (window.__imageClick)
                      return (window.__imageClick = false);
                    const target = params.event?.target;
                    if (
                      target?.closest(".audio-control-btn") ||
                      target?.closest(".wrapper") ||
                      target?.closest(".render-meta")
                    ) {
                      return;
                    }
                    if (params?.eventPath?.[0]?.localName === "input") {
                      return;
                    }
                    if (
                      params?.column?.getColId() ===
                      APP_CONSTANTS.AG_GRID_SELECTION_COLUMN
                    ) {
                      const selected = params.node.isSelected();
                      params.node.setSelected(!selected);
                      return;
                    }

                    debounceCellClick(() => {
                      setActiveRow(params.node.rowIndex);
                      setCurrentColumn(params?.colDef);
                      if (params.node.rowPinned !== "bottom") {
                        setDatapointDrawerData({
                          ...params?.colDef?.col,
                          rowData: params.data,
                          valueInfos:
                            params?.data[params?.colDef?.col?.id]?.valueInfos,
                          index: params.rowIndex,
                          rowIndexData: params.rowIndex,
                        });
                        // handleDatapoint(params?.data?.rowId);
                      }
                    }, params);
                  }}
                  onRowSelected={(event) => {
                    trackEvent(Events.rowSelected);
                    onSelectionChanged(event);
                  }}
                  getRowClass={(params) =>
                    params.node.rowIndex === activeRow ? "active-row" : ""
                  }
                  className="develop-data-grid"
                  suppressColumnMoveAnimation={true}
                  suppressColumnVirtualisation={true}
                />
              </>
            ) : (
              <AddRowData dataset={dataset} />
            )}
          </SingleImageViewerProvider>
        </Box>
      </AudioPlaybackProvider>
    );
  },
);

DevelopData.displayName = "DevelopData";

DevelopData.propTypes = {
  columnDefs: PropTypes.array,
  setColumnDefs: PropTypes.func,
  setColumns: PropTypes.func,
  setDevelopFilterOpen: PropTypes.func,
  developFilterOpen: PropTypes.bool,
  setConfigureEval: PropTypes.func,
  onSelectionChanged: PropTypes.func,
  renderAppData: PropTypes.bool,
  selectedAll: PropTypes.bool,
  setSelectedAll: PropTypes.any,
  searchQuery: PropTypes.string,
  cellHeight: PropTypes.string,
  setIsFilterApplied: PropTypes.func,
  isData: PropTypes.bool,
  setIsData: PropTypes.func,
  setIsRows: PropTypes.func,
  gridApiRef: PropTypes.any,
};

export default DevelopData;
