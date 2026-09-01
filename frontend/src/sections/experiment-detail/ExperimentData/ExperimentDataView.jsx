import { Box } from "@mui/material";
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
import { useParams } from "react-router";
import { ExperimentDataDefaultColDef, getEachColumnDef } from "./tableConfig";
import CustomExperimentGroupHeader from "./CustomExperimentGroupHeader";
import ExperimentDetailDrawer from "./ExperimentDetailDrawer";
import { useExperimentDetailContext } from "../experiment-context";
import { menuIcons } from "src/utils/MenuIconSet/svgIcons";
import { preventHeaderSelection } from "src/utils/utils";
import "./ExperimentDataViewStyle.css";
import ExperimentStatusBar from "./ExperimentStatusBar";
import EvaluationDrawer from "src/sections/common/EvaluationDrawer/EvaluationDrawer";
import { defaultRowHeightMapping } from "src/utils/constants";
import ExperimentBarDataRightSection from "../ExperimentBarRightSection/ExperimentBarDataRightSection";
import logger from "src/utils/logger";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import useWavesurferCache from "src/hooks/use-wavesurfer-cache";
import { toExcelLetters, shouldShowDiffModeButton } from "./Common";

import {
  useConfigureEvalStore,
  useEditColumnNameStore,
  useShowSummaryStore,
  useShowSummaryStoreShallow,
} from "../../develop-detail/states";
import EditEvaluation from "src/sections/develop-detail/Evaluation/EditEvaluation";
import EditColumnName from "../../develop-detail/DataTab/EditColumnName";
import { isEqual } from "lodash";
import PdfPreviewDrawer from "src/components/PdfPreviewDrawer";
import { APP_CONSTANTS } from "src/utils/constants";
import FormSearchField from "src/components/FormSearchField/FormSearchField";

import { enqueueSnackbar } from "notistack";
import SingleImageViewerProvider from "src/sections/develop-detail/Common/SingleImageViewer/SingleImageViewerProvider";
import CompositeEvalDialog from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/CompositeEvalDialog";
import RenderCellRunningOptions from "./RenderCellRunningOptions";
import { useRerunColumnInExperimentStoreShallow } from "./states";
import { useGetExperimentDetails } from "src/hooks/useGetExperimentDetails";
// Constants
// const RefreshStatus = ["Running", "NotStarted", "ExperimentEvaluation"]; // Status values that trigger refresh

const defaultColumnDefs = [
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
];
/**
 * ExperimentDataView Component - Displays experiment data in a grid
 */
function ExperimentDataView() {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.dataGridPadded);
  // URL and Context Hooks
  const { experimentId } = useParams();
  const {
    setEvaluateOpen,
    evaluateOpen,
    diffMode,
    setFetchingData,
    experimentDetailColumnSize,
    setExperimentGridRef,
    showAllColumns,
    viewAllPrompts,
  } = useExperimentDetailContext();
  const { data: experimentData, isLoading: isExperimentDetailLoading } =
    useGetExperimentDetails(experimentId);
  const [experimentSearch, setExperimentSearch] = useState("");
  // const columnConfigureRef = useRef(null);
  // const resizerRef = useRef(null);
  // const [cellHeight, setCellHeight] = useState("Short");
  // Refs

  const gridApiRef = useRef(null);
  const isRefreshingColumns = useRef(null);
  const columnCountRef = useRef(0);

  // State management
  const [columnDefs, setColumnDefs] = useState(defaultColumnDefs);
  const showSummary = useShowSummaryStoreShallow((s) => {
    return s.showSummary;
  });

  const [expandRow, setExpandRow] = useState(null);
  const [columnConfig, setColumnConfig] = useState([]);
  const [experimentMeta, setExperimentMeta] = useState(null);
  const [pinnedBottomRowData, setPinnedBottomRowData] = useState([]);
  const [columnAvgs, setColumnAvgs] = useState([]);
  const [allRows, setAllRows] = useState([]);
  const [columnsInitialized, setColumnsInitialized] = useState(false);
  const [totalRows, setTotalRows] = useState(0);
  const queryClient = useQueryClient();

  // Prevent text selection in grid headers
  preventHeaderSelection();

  const {
    clearWaveSurferCache,
    getWaveSurferInstance,
    removeWaveSurferInstance,
    storeWaveSurferInstance,
    updateWaveSurferInstance,
  } = useWavesurferCache();

  const { mutate: onRerun } = useMutation({
    mutationFn: ({ columnId, rowId }) =>
      axios.post(
        endpoints.develop.experiment.reRunExperimentCell(experimentId),
        {
          cells: [{ column_id: columnId, row_id: rowId }],
          failed_only: true,
        },
      ),
    onSuccess: () => {
      gridApiRef?.current?.api?.refreshServerSide({});
      enqueueSnackbar("Experiment cell re-run successfully initiated", {
        variant: "success",
      });
    },
  });

  useEffect(() => {
    // Return a cleanup function to clear cache on component unmount
    return () => {
      clearWaveSurferCache();
    };
  }, [clearWaveSurferCache]); // Dependency array includes the stable callback

  // Reset state when experiment changes
  useEffect(() => {
    // Reset state when experiment changes
    setColumnsInitialized(false);
    setAllRows([]);
    columnCountRef.current = 0;
    isRefreshingColumns.current = null;
    // to show default loading
    setColumnDefs(defaultColumnDefs);
  }, [experimentId]);

  const { data: columnConfigData, refetch: refetchExperimentColumns } =
    useQuery({
      queryFn: () =>
        axios.get(endpoints.develop.experiment.experimentDetail(experimentId), {
          params: { column_config_only: true },
        }),
      queryKey: ["experiment-column-config", experimentId],
      refetchOnWindowFocus: false,
      refetchOnMount: true,
      refetchOnReconnect: false,
      select: (data) => data.data,
      enabled: !!experimentId,
    });
  // Memoize storeAllDisplayedRows to prevent recreation on every render
  const storeAllDisplayedRows = useCallback(() => {
    if (!gridApiRef.current?.api) return;

    const newRows = [];
    gridApiRef.current.api.forEachNode((node) => {
      if (node.displayed) {
        newRows.push(node);
      }
    });

    setAllRows((prev) => {
      // Early return if no new rows
      if (newRows.length === 0) return prev;

      const mergedMap = new Map();

      // Use unique row ID instead of rowIndex
      prev.forEach((row) => {
        const id = row.data?.row_id;
        if (id !== undefined) {
          mergedMap.set(id, row);
        }
      });

      newRows.forEach((row) => {
        const id = row.data?.row_id;
        if (id !== undefined) {
          mergedMap.set(id, row);
        }
      });

      // Only update state if there are actual changes
      const mergedArray = Array.from(mergedMap.values());
      if (mergedArray.length !== prev.length) {
        return mergedArray;
      }

      // Check if any rows actually changed
      const prevMap = new Map(
        prev.map((row) => [row.data?.row_id, row]),
      );
      const hasChanges = newRows.some(
        (newRow) =>
          !isEqual(
            prevMap.get(newRow.data?.row_id),
            newRow,
          ),
      );

      return hasChanges ? mergedArray : prev;
    });
  }, []);

  // Helper function to process columns in a group
  const processColumnsInGroup = useCallback((columnsInGroup, summaryState) => {
    return columnsInGroup
      .filter((col) => {
        // Skip reason columns unless they should be shown
        if (col?.col?.name?.includes("-reason")) {
          const findInShowSummary = summaryState?.find(
            (v) => v === col?.col?.group?.id,
          );

          return findInShowSummary !== undefined;
        }
        return true;
      })
      .map((col) => {
        return {
          ...col,
          // headerTooltip: col?.headerTooltip || col?.headerName || col?.col?.name,
          width: 300,
          headerComponentParams: {
            ...col.headerComponentParams,
            isGrouped: true,
          },
          headerClass: "child-column-header",
        };
      });
  }, []);

  // Process column data - with stability fixes while maintaining original refresh mechanism
  const setColumnData = useCallback(
    (data, setCols = true, _skipColumnCountCheck = false) => {
      // Extract data once to avoid multiple accesses
      const columns = data?.result?.column_config || data?.result?.columnConfig;
      // const rows = data?.result?.table || [];

      setColumnConfig(columns);

      // Setup for column groups and refresh tracking
      const columnsByGroup = {};

      // Process each column for refreshing and grouping
      for (const eachCol of columns) {
        // Group columns
        if (eachCol?.group?.id) {
          if (!columnsByGroup[eachCol?.group?.id]) {
            columnsByGroup[eachCol?.group?.id] = [];
          }
          columnsByGroup[eachCol?.group?.id].push(
            getEachColumnDef(
              eachCol,
              getWaveSurferInstance,
              storeWaveSurferInstance,
              removeWaveSurferInstance,
              updateWaveSurferInstance,
              onRerun,
            ),
          );
        }
      }


      for (const groupId in columnsByGroup) {
        const cols = columnsByGroup[groupId] ?? [];

        columnsByGroup[groupId] = [...cols].sort((a, b) => {
          const nameA = a?.headerName ?? "";
          const nameB = b?.headerName ?? "";

          const baseA = nameA.replace(/-reason.*/, "");
          const baseB = nameB.replace(/-reason.*/, "");

          if (baseA !== baseB) {
            return baseA.localeCompare(baseB);
          }

          const aIsReason = nameA.endsWith("-reason");
          const bIsReason = nameB.endsWith("-reason");

          if (aIsReason && !bIsReason) return 1;
          if (!aIsReason && bIsReason) return -1;

          return 0;
        });
      }

      // Process grouped columns - organize in order: base columns, grouped columns, other columns
      const columnMap = [];
      const commonHeaderClass = "custom-header";

      // Separate single and grouped columns
      const singleBaseColumns = [];
      const singleNonBaseColumns = [];
      const groupedColumns = [];
      Object.entries(columnsByGroup).forEach(([_, columnsInGroup]) => {
        const col = columnsInGroup?.[0];
        const isBaseColumn = col?.col?.is_base_column || col?.col?.isBaseColumn;
        const originType =
          col?.originType || col?.col?.origin_type || col?.col?.originType;
        const isExperimentType =
          originType === "experiment" || originType === "Experiment";

        const isEvalType =
          originType === "evaluation" || originType === "Evaluation";
        if (columnsInGroup.length === 1 && !isExperimentType && !isEvalType) {
          // Single column without experiment/evaluation origin - treat as single
          if (isBaseColumn) {
            singleBaseColumns.push(col);
          } else {
            singleNonBaseColumns.push(col);
          }
        } else {
          let children = processColumnsInGroup(columnsInGroup, showSummary);

          // Hide only agent columns where isFinal is false
          // Show all other columns
          children = viewAllPrompts
            ? children
            : children.filter(
                (child) => !(child?.col?.isAgent && !child?.col?.isFinal),
              );
          if (children.length > 0) {
            groupedColumns.push({
              headerName: children[0].col.group.name,
              headerGroupComponent: CustomExperimentGroupHeader,
              children,
              width: 300,
              headerGroupComponentParams: {
                group: children[0].col.group,
              },
              headerTooltip: children[0].col.name,
              headerClass: commonHeaderClass,
            });
          }
        }
      });
      // Add columns in order: base columns, grouped columns, then non-base columns (if showAllColumns)
      columnMap.push(...singleBaseColumns);
      columnMap.push(...groupedColumns);
      if (showAllColumns) {
        columnMap.push(...singleNonBaseColumns);
      }

      const columnWithHeadAndIndex = columnMap.map((col) => {
        if (col?.children?.length > 0) {
          let letterIndex = 0;

          for (let i = 0; i < col.children.length; i++) {
            const child = col.children[i];
            const colOrigin = child?.col?.group?.origin;
            const colOriginType = child?.col?.origin_type;
            const childName = child?.col?.name ?? child?.headerName ?? "";
            const isReasonColumn =
              childName.includes("-reason") ||
              colOriginType === "evaluation_reason";
            const isBaseEvalColumn =
              colOrigin === "Evaluation" && !child?.col?.source_id;

            const skipLetter =
              colOrigin === "Dataset" ||
              isReasonColumn ||
              isBaseEvalColumn ||
              (colOrigin === "Evaluation" && col.children.length === 1);
            const letter = skipLetter ? "" : toExcelLetters(letterIndex++);

            child.headerComponentParams = {
              ...child.headerComponentParams,
              head: letter,
              index: letterIndex,
            };
          }
        }
        return col;
      });

      // IMPORTANT: Only update the column definitions if we need to
      // This ensures column stability
      if (setCols) {
        setColumnDefs(columnWithHeadAndIndex);
        setColumnsInitialized(true);
        columnCountRef.current = columns.length;
      }

      // Update rows with proper replacement to avoid duplicates
      // setAllRows(rows);

      // Calculate averages for bottom pinned row
      const filteredColumns = columnMap.flatMap((col) =>
        col.children
          ? col.children.filter((child) => {
              const avg = child?.col?.average_score ?? child?.col?.averageScore;
              return avg !== null && avg !== undefined;
            })
          : (() => {
                const avg = col?.col?.average_score ?? col?.col?.averageScore;
                return avg !== null && avg !== undefined;
              })()
            ? [col]
            : [],
      );

      const pinnedRow = {};
      filteredColumns.forEach((col) => {
        if (col.field) {
          const avgValue = `${(col.col.average_score ?? col.col.averageScore).toFixed(2)}%`;
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
    },
    [
      showSummary,
      processColumnsInGroup,
      columnsInitialized,
      showAllColumns,
      viewAllPrompts,
    ],
  );

  // Refresh grid function
  const refreshGrid = (options = {}, refreshCols) => {
    if (gridApiRef.current?.api) {
      gridApiRef.current.api.refreshServerSide(options ?? {});
    }
    if (refreshCols) {
      refreshColumns();
      refreshDatasetColumns();
    }
  };

  // Refresh rows manually - optimized with column stability while keeping original polling
  const refreshRowsManual = useCallback(async () => {
    if (!gridApiRef?.current?.api) return;

    const cacheState = gridApiRef.current.api.getCacheBlockState();
    if (!cacheState) return;

    const totalPages = Object.keys(cacheState).length;

    // need to update cache
    const columnsData = (await refetchExperimentColumns())?.data;

    const newColumnCount =
      (columnsData?.result?.column_config || columnsData?.result?.columnConfig)
        ?.length || 0;
    // Update columnCountRef to track changes
    columnCountRef.current = newColumnCount;

    // MATCH ORIGINAL REFRESH BEHAVIOR: Process each page sequentially
    for (let p = 0; p < totalPages; p++) {
      try {
        const { data } = await axios.get(
          endpoints.develop.experiment.experimentDetail(experimentId),
          { params: { current_page_index: p, get_diff: diffMode } },
        );

        const rows = data?.result?.table || [];
        isRefreshingColumns.current = data?.result?.status !== "Completed";

        if (gridApiRef.current?.api) {
          // Apply server-side transaction exactly as in original code
          const transaction = { update: rows };
          if (gridApiRef.current?.api) {
            gridApiRef.current.api.applyServerSideTransaction(transaction);
          }
        }
        // Process column data
      } catch (error) {
        logger.warn(`Error refreshing page ${p}:`, error);
      }
    }
  }, [experimentId, diffMode, refetchExperimentColumns]);

  // POLLING: Keep the original interval behavior exactly as in the original code
  useEffect(() => {
    const interval = setInterval(() => {
      if (isRefreshingColumns.current) {
        refreshRowsManual();
      }
    }, 10000);

    return () => clearInterval(interval);
  }, [diffMode, refreshRowsManual]);

  // Menu configurations
  // Menu configurations - updated to handle both columns and column groups
  const menuList = useCallback((params) => {
    const extraMenuItems = [];

    // Handle column group menus
    if (params.columnGroup) {
      const children = params.columnGroup.getLeafColumns();
      if (children && children.length > 0) {
        const firstChild = children[0];
        const column = firstChild?.getColDef()?.col;

        const colOriginType = column?.origin_type || column?.originType;
        if (colOriginType === "Evaluation" || colOriginType === "evaluation") {
          const { showSummary, toggleSummary } = useShowSummaryStore.getState();
          extraMenuItems.push({
            name: showSummary.includes(column?.group?.id)
              ? "Hide Reasoning"
              : "Show Reasoning",
            action: () => {
              toggleSummary({ id: column?.group?.id });
            },
            icon: menuIcons["Show Reasoning"],
          });
          extraMenuItems.push({
            name: "Configure Eval",
            action: () => {
              useConfigureEvalStore.setState({
                configureEval: {
                  id: column?.group?.id,
                  evalType: "user",
                },
              });
            },
            icon: menuIcons["Configure Eval"],
          });
          extraMenuItems.push({
            name: "Edit Column Name",
            action: () => {
              useEditColumnNameStore.setState({
                editColumnName: column,
              });
            },
            icon: menuIcons["Edit Column Name"],
          });
        }
      }
      return extraMenuItems;
    }

    // Handle regular column menus
    if (!params?.column || !params.column.colDef) {
      return extraMenuItems;
    }

    const column = params.column.colDef.col;

    const colOriginType2 = column?.origin_type || column?.originType;
    if (colOriginType2 === "Evaluation" || colOriginType2 === "evaluation") {
      const { showSummary, toggleSummary } = useShowSummaryStore.getState();
      extraMenuItems.push({
        name: showSummary.includes(column?.group?.id)
          ? "Hide Reasoning"
          : "Show Reasoning",
        action: () => {
          toggleSummary({ id: column?.group?.id });
        },
        icon: menuIcons["Show Reasoning"],
      });
      extraMenuItems.push({
        name: "Configure Eval",
        action: () => {
          useConfigureEvalStore.setState({
            configureEval: {
              id: column?.group?.id,
              evalType: "user",
            },
          });
        },
        icon: menuIcons["Configure Eval"],
      });
      extraMenuItems.push({
        name: "Edit Column Name",
        action: () => {
          useEditColumnNameStore.setState({
            editColumnName: column,
          });
        },
        icon: menuIcons["Edit Column Name"],
      });
    }

    return [...extraMenuItems];
  }, []);

  // Post-process popup styling
  // const postProcessPopup = useCallback((params) => {
  //   if (params.type !== "columnMenu") return;

  //   const ePopup = params.ePopup;
  //   ePopup.style.backgroundColor = "#fff";
  //   ePopup.style.borderRadius = "12px";
  //   ePopup.style.border = "none";

  //   const elements = ePopup.querySelectorAll('span[data-ref="eName"]');
  //   elements.forEach((element) => {
  //     if (
  //       element.textContent.trim() === "Show Summary" ||
  //       element.textContent.trim() === "Hide Summary"
  //     ) {
  //       element.style.color = "#7857FC";
  //     }
  //     element.style.fontWeight = 400;
  //   });
  // }, []);

  // Grid options configuration
  const gridOptions = useMemo(
    () => ({
      tooltipShowDelay: 0,
      enableBrowserTooltips: false,
      getRowStyle: (params) => {
        if (params.node.rowPinned === "bottom") {
          return { height: "30px !important" };
        }

        return null;
      },
    }),
    [],
  );

  // Memoize getRowHeight to prevent recreation on every render
  const getRowHeight = useCallback(
    (params) => {
      return params.node.rowPinned === "bottom"
        ? 40
        : defaultRowHeightMapping[experimentDetailColumnSize]?.height;
    },
    [experimentDetailColumnSize],
  );

  // All column options for evaluation
  const allColumnOptions = useMemo(() => {
    return columnConfig.reduce((acc, curr) => {
      const currOriginType = curr?.origin_type || curr?.originType;
      if (
        currOriginType !== "evaluation" &&
        currOriginType !== "experiment" &&
        !curr?.name?.startsWith("Experiment")
      ) {
        acc.push({
          headerName: curr.name,
          field: curr.id,
          originType: currOriginType,
          dataType: curr?.data_type || curr?.dataType,
        });
      }
      return acc;
    }, []);
  }, [columnConfig]);

  // Handler for row clicks
  const handleRowClick = useCallback(
    (params) => {
      // Prevent actions when audio is clicked
      // Access window properties dynamically to avoid TypeScript errors
      if (window["__fileClick"]) {
        window["__fileClick"] = false;
        return;
      }
      if (window["__audioClick"]) {
        window["__audioClick"] = false;
        return;
      }
      if (window["__jsonViewerClick"]) {
        window["__jsonViewerClick"] = false;
        return;
      }
      if (window["__reRunClick"]) {
        window["__reRunClick"] = false;
        return;
      }
      if (window.__imageClick) return (window.__imageClick = false);

      const target = params.event?.target;

      // Skip if clicking on specific controls
      if (
        target?.closest(".audio-control-btn") ||
        target?.closest(".wrapper") ||
        target?.closest(".render-meta")
      ) {
        return;
      }

      // Handle bottom pinned rows differently
      if (params?.rowPinned !== "bottom") {
        // Handle checkbox column clicks
        if (
          params.event.target.closest(
            `.ag-cell[col-id="${APP_CONSTANTS.AG_GRID_SELECTION_COLUMN}"]`,
          )
        ) {
          const selected = params.node.isSelected();
          params.node.setSelected(!selected);
          return;
        }

        // Expand row details - only update if actually changed
        const newExpandRow = {
          ...params.data,
          index: params.rowIndex,
        };

        // Avoid unnecessary state updates if the same row is clicked
        const prevRowKey = expandRow?.row_id;
        const newRowKey = newExpandRow.row_id;
        if (
          prevRowKey !== newRowKey ||
          expandRow?.index !== newExpandRow.index
        ) {
          setExpandRow(newExpandRow);
        }
      }
    },
    [expandRow],
  );

  // Handler for column header clicks
  const handleColumnHeaderClick = useCallback((event) => {
    if (
      event.column.colId === APP_CONSTANTS.AG_GRID_SELECTION_COLUMN &&
      event.api
    ) {
      const displayedNodes = [];
      event.api.forEachNode((node) => {
        if (node.displayed) {
          displayedNodes.push(node);
        }
      });

      const allSelected = displayedNodes.every((node) => node.isSelected());

      if (allSelected) {
        event.api.deselectAll();
      } else {
        event.api.selectAll();
      }
    }
  }, []);

  useEffect(() => {
    if (!columnConfigData) return;
    setColumnData(columnConfigData, true);
    setColumnConfig(
      columnConfigData?.result?.column_config ||
        columnConfigData?.result?.columnConfig,
    );
  }, [
    columnConfigData,
    setColumnConfig,
    showSummary,
    showAllColumns,
    setColumnData,
  ]);

  const refreshColumns = () => {
    queryClient.invalidateQueries({
      queryKey: ["experiment-column-config", experimentId],
      type: "all",
    });
  };

  const refreshDatasetColumns = () => {
    queryClient.invalidateQueries({
      queryKey: ["dataset-column-config", experimentMeta?.dataset],
      type: "all",
    });
  };
  useEffect(() => {
    setExperimentGridRef(gridApiRef?.current);
  }, [gridApiRef, setExperimentGridRef]);

  // Data source configuration with column stability while maintaining refresh behavior
  const dataSource = useMemo(
    () => ({
      getRows: async (params) => {
        setFetchingData(true);

        const { request } = params;
        const pageNumber = Math.floor(request.startRow / 10);

        try {
          const { data } = await axios.get(
            endpoints.develop.experiment.experimentDetail(experimentId),
            {
              params: {
                current_page_index: pageNumber,
                get_diff: diffMode,
                search: experimentSearch,
              },
            },
          );

          // Set column data with the appropriate flags
          // setColumnData(data);
          const rows = data?.result?.table || [];
          const metadata = data?.result?.metadata;

          // Update experiment metadata
          setExperimentMeta(metadata);
          setTotalRows(data?.result?.metadata?.total_rows);
          isRefreshingColumns.current = data?.result?.status !== "Completed";
          params.success({
            rowData: rows,
            rowCount: metadata?.total_rows,
          });
          storeAllDisplayedRows();
        } catch (error) {
          logger.error("Error fetching experiment data:", error);
          isRefreshingColumns.current = null;
          params.fail();
        } finally {
          setFetchingData(false);
        }
      },
    }),
    [experimentId, diffMode, setFetchingData, experimentSearch],
  );

  // Status bar configuration
  const statusBar = useMemo(
    () => ({
      statusPanels: [
        {
          statusPanel: ExperimentStatusBar,
          align: "left",
          statusPanelParams: { columnAvgs },
        },
      ],
    }),
    [columnAvgs],
  );
  const selectedSourceId = useRerunColumnInExperimentStoreShallow(
    (state) => state.selectedSourceId,
  );
  const setSelectedSourceId = useRerunColumnInExperimentStoreShallow(
    (state) => state.setSelectedSourceId,
  );
  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        flex: 1,
        paddingX: "12px",
        height: "100%",
        overflowY: "hidden",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: (theme) => theme.spacing(2),
          // px: (theme) => theme.spacing(1.5),
          mb: (theme) => theme.spacing(1),
        }}
      >
        <FormSearchField
          size="small"
          placeholder="Search"
          searchQuery={experimentSearch}
          onChange={(e) => setExperimentSearch(e.target.value)}
          sx={{ width: "300px" }}
        />

        {/* <TableFilterOptions
          columnConfigureRef={columnConfigureRef}
          // setDevelopFilterOpen={setDevelopFilterOpen}
          // setOpenColumnConfigure={setOpenColumnConfigure}
          // setSearchQuery={setSearchQuery}
          resizerRef={resizerRef}
          gridApiRef={gridApiRef}
          setCellHeight={(key) => setCellHeight(key)}
          hideSearch
          // hideRowHeight
          hideColumnView
          hideFilter
          isData={gridApiRef?.current?.api?.getDisplayedRowCount?.() || 0}
        /> */}
        <ExperimentBarDataRightSection
          gridApiRef={gridApiRef}
          columns={columnConfig}
          outputFormat={
            columnConfigData?.result?.output_format ||
            columnConfigData?.result?.outputFormat
          }
          experimentData={experimentData}
        />
      </Box>
      {/* Detail drawer for expanded rows */}
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
        diffMode={diffMode}
        showDiffModeButton={shouldShowDiffModeButton(experimentData)}
      />

      {/* Evaluation drawer — same as dataset */}
      {experimentData?.dataset_id && (
        <EvaluationDrawer
          open={evaluateOpen}
          onClose={() => setEvaluateOpen(false)}
          allColumns={allColumnOptions?.filter(
            (col) => col?.originType !== "evaluation",
          )}
          refreshGrid={refreshGrid}
          module="experiment"
          id={experimentData.dataset_id}
          showAdd
          showTest
          runLabel="Add & Run"
        />
      )}

      {/* Main grid */}
      <Box
        className="experiment-view-grid"
        sx={{
          height: "100%",
        }}
      >
        <SingleImageViewerProvider>
          <CompositeEvalDialog />
          <AgGridReact
            rowHeight={defaultRowHeightMapping["Short"]?.height}
            getRowHeight={getRowHeight}
            ref={gridApiRef}
            columnDefs={columnDefs}
            onColumnHeaderClicked={handleColumnHeaderClick}
            defaultColDef={ExperimentDataDefaultColDef}
            paginationAutoPageSize={true}
            suppressRowClickSelection={true}
            paginationPageSizeSelector={false}
            suppressServerSideFullWidthLoadingRow={true}
            serverSideInitialRowCount={10}
            rowModelType="serverSide"
            isApplyServerSideTransaction={() => true}
            serverSideDatasource={dataSource}
            maxBlocksInCache={10}
            pagination={false}
            cacheBlockSize={10}
            statusBar={statusBar}
            pinnedBottomRowData={pinnedBottomRowData}
            gridOptions={gridOptions}
            getMainMenuItems={menuList}
            debounceVerticalScrollbar={true}
            // postProcessPopup={postProcessPopup}
            getRowId={({ data }) => data.row_id}
            theme={agTheme}
            onRowClicked={handleRowClick}
            suppressColumnMoveAnimation={true}
          />
        </SingleImageViewerProvider>
      </Box>
      <EditEvaluation
        onSuccess={() => {
          refreshGrid(null, true);
          refetchExperimentColumns();
        }}
        module={"update-experiment"}
      />
      <PdfPreviewDrawer />
      <RenderCellRunningOptions
        sourceId={selectedSourceId}
        open={Boolean(selectedSourceId)}
        gridApiRef={gridApiRef}
        onClose={() => {
          setSelectedSourceId(null);
        }}
      />
      <EditColumnName
        onSuccess={() => {
          refreshGrid(null, true);
          refetchExperimentColumns();
        }}
      />
    </Box>
  );
}

export default ExperimentDataView;
